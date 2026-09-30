from __future__ import annotations

"""Phase 0 of the LLM thesis validator: capture the entry thesis.

Every executed option entry already carries the strategist recommendation
(catalyst, thesis, risks, grounded article citations) and the manager reason.
This module distills that into a structured, position-keyed thesis record so
the daily adjudicator has something concrete to check against. It never calls
a model and never blocks trade execution.
"""

import logging
from datetime import date, datetime
from typing import Any

from services.option_dte_buckets import (
    OPTION_DTE_BUCKET_BY_KEY,
    TIME_HORIZON_TO_DTE_BUCKET,
    resolve_dte_bucket_for_days,
)
from db_helpers.market import close_position_thesis
from db_helpers.market import record_position_thesis
from services.pricing_policy import HORIZON_DAYS


LOGGER = logging.getLogger(__name__)

THESIS_CAPTURE_VERSION = "thesis-capture-v2-catalyst-horizon"

CATALYST_SUMMARY_MAX_CHARS = 300
MANAGER_REASON_MAX_CHARS = 600
MAX_INVALIDATION_CONDITIONS = 3

# Keyword classifier for catalyst_type. First match wins; "other" is the floor.
_CATALYST_TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("earnings_surprise", ("earnings", "eps", "quarterly results", "beat", "miss", "revenue")),
    ("guidance", ("guidance", "outlook", "forecast", "raised", "lowered")),
    ("mna", ("merger", "acquisition", "acquire", "takeover", "buyout", "deal")),
    ("regulatory", ("regulator", "regulatory", "fda", "sec ", "antitrust", "approval", "tariff", "sanction")),
    ("product", ("launch", "product", "unveil", "release", "chip", "model", "platform")),
    ("analyst_action", ("upgrade", "downgrade", "price target", "initiat", "analyst")),
    ("legal", ("lawsuit", "litigation", "settlement", "court", "ruling")),
    ("macro", ("fed", "rates", "inflation", "macro", "economy", "jobs report")),
)


def _clean_sentence(value: Any, *, max_chars: int = CATALYST_SUMMARY_MAX_CHARS) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:max_chars]


def _classify_catalyst_type(*texts: Any) -> str:
    combined = " ".join(str(text or "") for text in texts).lower()
    if not combined.strip():
        return "unknown"
    for catalyst_type, keywords in _CATALYST_TYPE_KEYWORDS:
        if any(keyword in combined for keyword in keywords):
            return catalyst_type
    return "other"


def _strategist_article_ids(strategist_recommendation: dict[str, Any]) -> list[int]:
    article_ids: set[int] = set()
    claims = strategist_recommendation.get("evidence_claims")
    if isinstance(claims, list):
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            for article_id in claim.get("evidence_article_ids") or []:
                if isinstance(article_id, int) and not isinstance(article_id, bool) and article_id > 0:
                    article_ids.add(article_id)
    return sorted(article_ids)


def _days_until(expiration_date_text: Any, *, today: date | None = None) -> int | None:
    text = str(expiration_date_text or "").strip()[:10]
    if not text:
        return None
    try:
        expiration = date.fromisoformat(text)
    except ValueError:
        return None
    return (expiration - (today or datetime.now().date())).days


def _expected_horizon_days(
    *,
    selected_expiration_date: Any,
    time_horizon: Any,
    today: date | None = None,
) -> int | None:
    """Days in which the thesis should have played out.

    Derived from the position's DTE bucket: the bucket's max DTE minus its
    forced-exit buffer is the longest the rules layer will ever hold it.
    """
    entry_dte = _days_until(selected_expiration_date, today=today)
    bucket = resolve_dte_bucket_for_days(entry_dte) if entry_dte is not None else None
    if bucket is None:
        bucket_key = TIME_HORIZON_TO_DTE_BUCKET.get(str(time_horizon or "").strip().lower())
        bucket = OPTION_DTE_BUCKET_BY_KEY.get(bucket_key or "")
    if bucket is None:
        return entry_dte if entry_dte is not None and entry_dte > 0 else None
    horizon = bucket.max_days - (bucket.force_exit_days_to_expiration or 0)
    if entry_dte is not None:
        horizon = min(horizon, entry_dte)
    return max(1, horizon)


def _build_invalidation_conditions(
    *,
    symbol: str,
    structure: str,
    catalyst_summary: str,
    expected_horizon_days: int | None,
    risks: list[str],
) -> list[str]:
    adverse_direction = "falls" if structure == "long_call" else "rises"
    conditions = [
        (
            f"This thesis is wrong if {symbol} {adverse_direction} versus entry on news specific to the "
            "company rather than a broad sector or index move."
        )
    ]
    if catalyst_summary:
        horizon_text = f" within {expected_horizon_days} days" if expected_horizon_days else ""
        conditions.append(
            "This thesis is wrong if the entry catalyst "
            f"({catalyst_summary}) is denied, reversed, or produces no follow-through{horizon_text}."
        )
    top_risk = _clean_sentence(risks[0]) if risks else ""
    if top_risk:
        conditions.append(f"This thesis is wrong if this entry risk materializes: {top_risk}")
    return conditions[:MAX_INVALIDATION_CONDITIONS]


def build_position_thesis_record(execution: dict[str, Any]) -> dict[str, Any] | None:
    """Build the Phase 0 thesis record from one successful trade execution."""
    if not isinstance(execution, dict):
        return None

    option_symbol = str(execution.get("selected_option_symbol") or "").strip().upper()
    symbol = str(execution.get("symbol") or "").strip().upper()
    decision = str(execution.get("decision") or "").strip().lower()
    if not option_symbol or not symbol or decision not in {"call", "put"}:
        return None

    strategist = execution.get("strategist_recommendation")
    strategist = strategist if isinstance(strategist, dict) else {}
    catalysts = [str(item).strip() for item in (strategist.get("catalyst") or []) if str(item).strip()]
    risks = [str(item).strip() for item in (strategist.get("risks") or []) if str(item).strip()]
    structure = "long_call" if decision == "call" else "long_put"

    catalyst_summary = _clean_sentence(
        catalysts[0] if catalysts else (strategist.get("why_now") or execution.get("reason"))
    )
    catalyst_type = _classify_catalyst_type(
        catalyst_summary,
        strategist.get("why_now"),
        strategist.get("summary"),
    )
    order = execution.get("order") if isinstance(execution.get("order"), dict) else {}
    opened_at = (
        order.get("submitted_at")
        or execution.get("submitted_at")
        or datetime.now().astimezone().isoformat()
    )
    expected_horizon_days = _expected_horizon_days(
        selected_expiration_date=execution.get("selected_expiration_date"),
        time_horizon=strategist.get("time_horizon"),
    )
    horizon_source = 'legacy_dte_bucket_fallback'
    state = ((execution.get('candidate_context') or {}).get('catalysts') or [{}])[0]
    if state.get('classification_status') == 'classified' and state.get('expected_horizon') in HORIZON_DAYS:
        expected_horizon_days = HORIZON_DAYS[state['expected_horizon']]
        horizon_source = 'classified_catalyst_calendar_prior'

    return {
        "candidate_id": execution.get("candidate_id"),
        "pipeline_version": (execution.get("candidate_context") or {}).get("pipeline_version"),
        "manager_decision_history_id": execution.get("manager_decision_history_id"),
        "trade_execution_order_id": str(order.get("id") or "").strip() or None,
        "company_id": execution.get("company_id"),
        "symbol": symbol,
        "option_symbol": option_symbol,
        "structure": structure,
        "opened_at": opened_at,
        "catalyst_type": catalyst_type,
        "catalyst_summary": catalyst_summary,
        "source_article_ids": _strategist_article_ids(strategist),
        "expected_horizon_days": expected_horizon_days,
        "invalidation_conditions": _build_invalidation_conditions(
            symbol=symbol,
            structure=structure,
            catalyst_summary=catalyst_summary,
            expected_horizon_days=expected_horizon_days,
            risks=risks,
        ),
        "raw_json": {
            'expected_horizon_source': horizon_source,
            "version": THESIS_CAPTURE_VERSION,
            "manager_reason": _clean_sentence(execution.get("reason"), max_chars=MANAGER_REASON_MAX_CHARS),
            "strategist_thesis": [
                _clean_sentence(item) for item in (strategist.get("thesis") or []) if str(item).strip()
            ],
            "strategist_catalysts": catalysts,
            "strategist_risks": risks,
            "strategist_time_horizon": str(strategist.get("time_horizon") or "").strip(),
            "expected_stock_direction": str(strategist.get("expected_stock_direction") or "").strip(),
        },
    }


class PositionThesisJournal:
    """Persist entry theses and close them out; mirrors OptionTradeJournal."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._logger = logger or LOGGER

    def record_entry(self, execution: dict[str, Any]) -> int | None:
        thesis = build_position_thesis_record(execution)
        if thesis is None:
            return None

        thesis_id = record_position_thesis(thesis)
        if thesis_id is not None:
            self._logger.info(
                "Recorded position thesis id=%s for %s (catalyst_type=%s, horizon_days=%s)",
                thesis_id,
                thesis["option_symbol"],
                thesis["catalyst_type"],
                thesis["expected_horizon_days"],
            )
        return thesis_id

    def record_exit(self, position_summary: dict[str, Any]) -> bool:
        if not isinstance(position_summary, dict) or not position_summary.get("close_submitted"):
            return False
        option_symbol = str(position_summary.get("symbol") or "").strip().upper()
        if not option_symbol:
            return False

        closed = close_position_thesis(
            option_symbol,
            closed_at=position_summary.get("last_action_at") or None,
        )
        if closed:
            self._logger.info("Closed position thesis for %s after managed exit.", option_symbol)
        return closed
