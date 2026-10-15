from __future__ import annotations

"""Public helper entrypoints for the manager stage.

The manager stage needs two kinds of data:
1. The strategist evidence package assembled from stored research.
2. The live market/account context assembled from Alpaca.

This module intentionally stays small and delegates the market-specific work to
``agent_helpers.market_context`` so callers only need one stable import surface.
"""

import json
from pathlib import Path
import sys
from typing import Any
from datetime import datetime

from services.settings import get_settings
from services.settings import load_process_settings


if __name__ == "__main__":
    load_process_settings()


from agent_builders.strategist_payload import (
    DEFAULT_FULL_ARTICLE_LIMIT,
    DEFAULT_MAX_ARTICLE_AGE_DAYS,
    DEFAULT_SUMMARY_ARTICLE_LIMIT,
    build_strategist_input,
)
from agent_helpers.market_context import (
    build_market_context,
)
from agent_contracts import ManagerInputPayload
from market_db_modules.autopsy import list_decision_autopsy_ledger
from market_db_modules.queries import list_recent_manager_decision_history


__all__ = [
    "DEFAULT_FULL_ARTICLE_LIMIT",
    "DEFAULT_MAX_ARTICLE_AGE_DAYS",
    "DEFAULT_SUMMARY_ARTICLE_LIMIT",
    "build_resolved_autopsy_insights",
    "build_manager_input",
    "build_market_context",
    "test_market_context",
]


def _compact_article_reference(article: dict[str, Any]) -> dict[str, Any]:
    return {
        "article_id": int(article.get("article_id") or 0),
        "title": str(article.get("title") or ""),
        "source": str(article.get("source") or ""),
        "published_at": str(article.get("published_at") or ""),
        "article_scope": str(article.get("article_scope") or ""),
        "evidence_layers": [
            str(layer or "").strip()
            for layer in article.get("evidence_layers", [])
            if str(layer or "").strip()
        ],
    }


def _extract_recent_history_article_references(
    manager_input_json: Any,
    *,
    article_limit: int,
) -> list[dict[str, Any]]:
    if isinstance(manager_input_json, str):
        try:
            manager_input_json = json.loads(manager_input_json)
        except json.JSONDecodeError:
            return []
    if not isinstance(manager_input_json, dict):
        return []

    article_references = manager_input_json.get("article_references")
    if isinstance(article_references, list):
        compact: list[dict[str, Any]] = []
        for item in article_references[: max(1, int(article_limit))]:
            if not isinstance(item, dict):
                continue
            compact.append(
                {
                    "article_id": int(item.get("article_id") or 0),
                    "title": str(item.get("title") or ""),
                    "source": str(item.get("source") or ""),
                    "published_at": str(item.get("published_at") or ""),
                    "article_scope": str(item.get("article_scope") or ""),
                    "evidence_layers": [
                        str(layer or "").strip()
                        for layer in item.get("evidence_layers", [])
                        if str(layer or "").strip()
                    ],
                }
            )
        return compact

    supporting_articles = manager_input_json.get("supporting_articles")
    if not isinstance(supporting_articles, dict):
        return []
    article_summaries = supporting_articles.get("article_summaries", [])
    if not isinstance(article_summaries, list):
        return []
    return [
        _compact_article_reference(article)
        for article in article_summaries[: max(1, int(article_limit))]
        if isinstance(article, dict)
    ]


def _build_recent_manager_decision_history(
    company: dict[str, Any],
    *,
    history_limit: int,
    history_max_age_days: int,
    history_article_limit: int,
) -> list[dict[str, Any]]:
    rows = list_recent_manager_decision_history(
        company_id=company.get("company_id"),
        underlying_symbol=company.get("symbol"),
        limit=max(1, int(history_limit)),
        within_days=max(1, int(history_max_age_days)),
    )
    summarized: list[dict[str, Any]] = []
    for row in rows:
        if row is None:
            continue
        item = dict(row)
        summarized.append(
            {
                "decision_run_at": str(item.get("decision_run_at") or ""),
                "manager_decision": str(item.get("manager_decision") or ""),
                "manager_confidence": str(item.get("manager_confidence") or ""),
                "target_dte_bucket": str(item.get("target_dte_bucket") or ""),
                "trade_executed": bool(item.get("trade_executed")),
                "latest_trade_pnl_pct": item.get("latest_trade_pnl_pct"),
                "resolved_outcome_label": str(item.get("resolved_outcome_label") or ""),
                "article_references": _extract_recent_history_article_references(
                    item.get("manager_input_json"),
                    article_limit=history_article_limit,
                ),
            }
        )
    return summarized


def build_resolved_autopsy_insights(
    company: dict[str, Any],
    *,
    limit: int,
    max_age_days: int,
    as_of: datetime | str | None = None,
    db_path: Path | str | None = None,
    exclude_decision_history_id: int | None = None,
) -> list[dict[str, Any]]:
    """Return outcome feedback that was resolved before a replay decision time."""
    try:
        rows = list_decision_autopsy_ledger(
            company_id=company.get("company_id"),
            symbol=company.get("symbol"),
            resolved_only=True,
            within_days=max(1, int(max_age_days)),
            as_of=as_of,
            limit=max(1, int(limit)),
            db_path=db_path,
        )
    except Exception:
        return []
    insights: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if int(item.get("manager_decision_history_id") or 0) == int(exclude_decision_history_id or 0):
            continue
        snapshot = _safe_json_object(item.get("snapshot_json"))
        manager_input = _safe_json_object(item.get("manager_input_json"))
        strategist = manager_input.get("strategist_recommendation") if isinstance(manager_input.get("strategist_recommendation"), dict) else {}
        risks = [str(risk).strip() for risk in strategist.get("risks", []) if str(risk).strip()][:2] if isinstance(strategist.get("risks"), list) else []
        article_ids = [
            int(article.get("article_id") or 0)
            for article in _extract_recent_history_article_references(manager_input, article_limit=3)
            if int(article.get("article_id") or 0) > 0
        ]
        pnl = item.get("latest_trade_pnl_pct")
        try:
            pnl = float(pnl) if pnl is not None else None
        except (TypeError, ValueError):
            pnl = None
        insights.append(
            {
                "autopsy_id": int(item.get("id") or 0),
                "decision_history_id": int(item.get("manager_decision_history_id") or 0),
                "decision_run_at": str(item.get("decision_run_at") or ""),
                "manager_decision": str(snapshot.get("manager_decision") or ""),
                "manager_confidence": str(snapshot.get("manager_confidence") or ""),
                "target_dte_bucket": str(snapshot.get("target_dte_bucket") or ""),
                "strategist_decision": str(snapshot.get("strategist_decision") or strategist.get("decision") or ""),
                "article_ids": article_ids,
                "outcome_pnl_pct": pnl,
                "outcome_label": str(item.get("resolved_outcome_label") or ""),
                "pre_trade_risks": risks,
                "what_proved_us_wrong": (
                    risks[0]
                    if pnl is not None and pnl < 0 and risks
                    else "No adverse outcome was recorded in the eligible review window."
                ),
            }
        )
    return insights


def _safe_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_manager_input(
    company_identifier: str,
    *,
    start_time: datetime | None,
    end_time: datetime | None,
    max_age_days: int | None,
    summary_article_limit: int,
    full_article_limit: int,
    option_expiration_date: str | None,
    option_expiration_date_gte: str | None,
    option_expiration_date_lte: str | None,
    option_strike_price_gte: float | None,
    option_strike_price_lte: float | None,
    option_contract_limit_per_type: int,
    recent_history_limit: int | None = None,
    recent_history_max_age_days: int | None = None,
    recent_history_article_limit: int | None = None,
    resolved_autopsy_limit: int | None = None,
    resolved_autopsy_max_age_days: int | None = None,
) -> ManagerInputPayload:
    """Build the full manager payload for one company.

    Usage:
        ``payload = build_manager_input("AAPL", start_time=None, end_time=None, ...)``

    The returned payload can be passed directly to
    ``agent_stages.manager.decide_company_option_position`` or inspected during
    local debugging.
    """
    if recent_history_limit is None:
        recent_history_limit = get_settings().manager_context.history_limit
    if recent_history_max_age_days is None:
        recent_history_max_age_days = get_settings().manager_context.history_max_age_days
    if recent_history_article_limit is None:
        recent_history_article_limit = get_settings().manager_context.history_article_limit
    if resolved_autopsy_limit is None:
        resolved_autopsy_limit = get_settings().manager_context.resolved_autopsy_limit
    if resolved_autopsy_max_age_days is None:
        resolved_autopsy_max_age_days = get_settings().manager_context.resolved_autopsy_max_age_days
    payload = build_strategist_input(
        company_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
        summary_article_limit=summary_article_limit,
        full_article_limit=full_article_limit,
    )
    payload["market_context"] = build_market_context(
        payload["company"],
        option_expiration_date=option_expiration_date,
        option_expiration_date_gte=option_expiration_date_gte,
        option_expiration_date_lte=option_expiration_date_lte,
        option_strike_price_gte=option_strike_price_gte,
        option_strike_price_lte=option_strike_price_lte,
        option_contract_limit_per_type=max(1, int(option_contract_limit_per_type)),
    )
    payload["recent_manager_decision_history"] = _build_recent_manager_decision_history(
        payload["company"],
        history_limit=recent_history_limit,
        history_max_age_days=recent_history_max_age_days,
        history_article_limit=recent_history_article_limit,
    )
    payload["resolved_autopsy_insights"] = build_resolved_autopsy_insights(
        payload["company"],
        limit=resolved_autopsy_limit,
        max_age_days=resolved_autopsy_max_age_days,
    )
    return payload


def test_market_context(
    company_identifier: str,
    *,
    option_expiration_date: str | None = None,
    option_expiration_date_gte: str | None = None,
    option_expiration_date_lte: str | None = None,
    option_strike_price_gte: float | None = None,
    option_strike_price_lte: float | None = None,
    option_contract_limit_per_type: int | None = None,
) -> dict[str, Any]:
    """Return a diagnostics-first snapshot of manager market inputs.

    Usage:
        Run this helper when you want to confirm Alpaca credentials, option
        availability, and account state before involving the LLM stage.
    """
    if option_contract_limit_per_type is None:
        option_contract_limit_per_type = get_settings().manager_context.option_chain_limit_per_type
    payload = build_strategist_input(
        company_identifier,
        start_time=None,
        end_time=None,
        max_age_days=DEFAULT_MAX_ARTICLE_AGE_DAYS,
        summary_article_limit=DEFAULT_SUMMARY_ARTICLE_LIMIT,
        full_article_limit=DEFAULT_FULL_ARTICLE_LIMIT,
    )
    company = payload["company"]
    market_context = build_market_context(
        company,
        option_expiration_date=option_expiration_date,
        option_expiration_date_gte=option_expiration_date_gte,
        option_expiration_date_lte=option_expiration_date_lte,
        option_strike_price_gte=option_strike_price_gte,
        option_strike_price_lte=option_strike_price_lte,
        option_contract_limit_per_type=max(1, int(option_contract_limit_per_type)),
    )

    stock_snapshot = market_context.get("current_stock_price", {})
    market_indices = market_context.get("market_indices", {})
    sector_etf = market_context.get("sector_etf", {})
    option_market = market_context.get("option_market", {})
    account_state = market_context.get("account_state", {})

    return {
        "company": company,
        "diagnostics": {
            "stock_price_available": bool(stock_snapshot.get("available")),
            "stock_price_error": str(stock_snapshot.get("error") or ""),
            "market_index_count_available": sum(
                1
                for snapshot in market_indices.values()
                if isinstance(snapshot, dict) and snapshot.get("available")
            ),
            "sector_etf_available": bool(sector_etf.get("available")),
            "sector_etf_error": str(sector_etf.get("error") or ""),
            "option_market_available": bool(option_market.get("available")),
            "option_market_error": str(option_market.get("error") or ""),
            "option_contract_count": int(option_market.get("contract_count") or 0),
            "account_state_available": bool(account_state.get("available")),
            "account_state_error": str(account_state.get("error") or ""),
            "matching_position_count": int(
                account_state.get("company_position_state", {}).get("matching_position_count") or 0
            ),
        },
        "market_context": market_context,
    }


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    print(json.dumps(test_market_context(target), ensure_ascii=True, indent=2))
