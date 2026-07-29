from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from statistics import median
from zoneinfo import ZoneInfo

from agent_pipeline.ranking import get_current_rankings
from db_helpers.market import (
    get_latest_market_regime_snapshot,
    get_telemetry_summary,
    list_decision_expectancy_rows,
    list_recent_manager_decision_history,
)
from db_helpers import DB_PATH, get_connection
from performance_metrics import (
    DEFAULT_ANNUAL_RISK_FREE_RATE,
    compute_daily_returns,
    compute_sharpe_metrics,
    compute_sortino_metrics,
    compute_total_return_pct,
    compute_trade_metrics,
    normalize_equity_curve,
)
from portfolio_history_service import fetch_portfolio_history

from api_support.alpaca import alpaca_data_get_json, alpaca_get_json
from api_support.common import (
    load_dte_exit_rule_configs,
    parse_datetime,
    read_json_payload,
    safe_json_loads,
    safe_float,
)
from api_support.context import (
    BOT_DOWN_THRESHOLD_SECONDS,
    DEFAULT_OPTION_ORDER_QTY,
    DAILY_OPTION_LOSS_LIMIT_DOLLARS,
    MAX_CONCURRENT_OPTION_POSITIONS,
    MAX_DEPLOYABLE_BUYING_POWER_PCT,
    MAX_OPTION_ORDER_QTY_MULTIPLIER,
    MAX_OPTION_SECTOR_EXPOSURE_PCT,
    OPTION_MANAGER_STATUS_PATH,
    OPTION_POSITION_MANAGEMENT_OUTPUT_PATH,
    OPTION_POSITION_SETTINGS,
    PER_ORDER_SIZING_BUYING_POWER_PCT,
    SCRIPT_STATUS_PATH,
)
from api_support.trades import build_trade_explanation_payload


def _build_empty_company_decisions_payload() -> dict:
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "last_updated_at": "",
        "company_count": 0,
        "companies": [],
    }


def _empty_company_decision_entry(*, symbol: str, name: str, last_updated_at: str) -> dict:
    return {
        "symbol": symbol,
        "name": name,
        "last_updated_at": last_updated_at,
        "strategist": {
            "decision": "",
            "confidence": "",
            "summary": "",
            "thesis": [],
            "risks": [],
        },
        "manager": {
            "decision": "",
            "confidence": "",
            "reason": "",
            "target_dte_bucket": "",
            "manager_decision_history_id": None,
            "selected_option_id": "",
            "selected_expiration_date": "",
            "selected_strike_price": None,
            "selected_option_source": "",
            "entry_gate": {},
            "market_regime_gate": {},
        },
        "diagnostics": {
            "historical_bars": {},
            "strategist_validation": {},
        },
        "manager_history": [],
    }


def _extract_article_references_from_manager_input(
    manager_input_json: object,
    *,
    limit: int = 3,
) -> list[dict]:
    if isinstance(manager_input_json, str):
        try:
            manager_input_json = json.loads(manager_input_json)
        except json.JSONDecodeError:
            return []
    if not isinstance(manager_input_json, dict):
        return []

    article_references = manager_input_json.get("article_references")
    if not isinstance(article_references, list):
        return []

    compact: list[dict] = []
    for item in article_references[: max(1, int(limit))]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "article_id": int(item.get("article_id") or 0),
                "title": str(item.get("title") or "").strip(),
                "source": str(item.get("source") or "").strip(),
                "published_at": str(item.get("published_at") or "").strip(),
                "article_scope": str(item.get("article_scope") or "").strip(),
                "evidence_layers": [
                    str(layer or "").strip()
                    for layer in item.get("evidence_layers", [])
                    if str(layer or "").strip()
                ],
            }
        )
    return compact


def _normalize_history_company_identity(item: dict, manager_input_json: object) -> tuple[str, str]:
    manager_input = manager_input_json if isinstance(manager_input_json, dict) else {}
    company = manager_input.get("company")
    if not isinstance(company, dict):
        company = {}

    symbol = str(item.get("symbol") or company.get("symbol") or "").strip().upper()
    name = str(item.get("company_name") or company.get("name") or "").strip()
    return symbol, name


def _extract_strategist_snapshot_from_manager_input(
    manager_input_json: object,
    *,
    fallback_decision: object = None,
) -> dict:
    manager_input = manager_input_json if isinstance(manager_input_json, dict) else {}
    strategist = manager_input.get("strategist_recommendation")
    if not isinstance(strategist, dict):
        strategist = {}

    return {
        "decision": str(strategist.get("decision") or fallback_decision or "").strip(),
        "confidence": str(strategist.get("confidence") or "").strip(),
        "summary": str(strategist.get("summary") or "").strip(),
        "thesis": [
            str(item).strip()
            for item in strategist.get("thesis", [])
            if str(item).strip()
        ]
        if isinstance(strategist.get("thesis"), list)
        else [],
        "risks": [
            str(item).strip()
            for item in strategist.get("risks", [])
            if str(item).strip()
        ]
        if isinstance(strategist.get("risks"), list)
        else [],
    }


def _extract_manager_snapshot_from_history_row(
    item: dict,
    manager_output_json: object,
) -> dict:
    manager_output = manager_output_json if isinstance(manager_output_json, dict) else {}
    recommendation = manager_output.get("recommendation")
    if not isinstance(recommendation, dict):
        recommendation = {}
    entry_gate = manager_output.get("entry_gate") if isinstance(manager_output.get("entry_gate"), dict) else {}
    market_regime_gate = manager_output.get("market_regime_gate") if isinstance(manager_output.get("market_regime_gate"), dict) else {}

    return {
        "decision": str(item.get("manager_decision") or recommendation.get("decision") or "").strip(),
        "confidence": str(item.get("manager_confidence") or recommendation.get("confidence") or "").strip(),
        "reason": str(item.get("manager_reason") or recommendation.get("reason") or "").strip(),
        "target_dte_bucket": str(item.get("target_dte_bucket") or recommendation.get("target_dte_bucket") or "").strip(),
        "manager_decision_history_id": item.get("id"),
        "selected_option_id": str(item.get("selected_option_id") or recommendation.get("selected_option_id") or "").strip(),
        "selected_expiration_date": str(
            item.get("selected_expiration_date") or recommendation.get("selected_expiration_date") or ""
        ).strip(),
        "selected_strike_price": safe_float(
            item.get("selected_strike_price")
            if item.get("selected_strike_price") not in (None, "")
            else recommendation.get("selected_strike_price")
        ),
        "selected_option_source": str(
            item.get("selected_option_source") or recommendation.get("selected_option_source") or ""
        ).strip(),
        "entry_gate": entry_gate,
        "market_regime_gate": market_regime_gate,
    }


def _extract_historical_bars_diagnostic(manager_input_json: object) -> dict:
    manager_input = manager_input_json if isinstance(manager_input_json, dict) else {}
    market_context = manager_input.get("market_context")
    if not isinstance(market_context, dict):
        market_context = {}
    underlying = market_context.get("underlying_price_history")
    if not isinstance(underlying, dict):
        return {}

    return {
        "available": bool(underlying.get("available")),
        "source": str(underlying.get("source") or "").strip(),
        "feed": str(underlying.get("feed") or "").strip(),
        "close_count": int(underlying.get("close_count") or 0),
        "error": str(underlying.get("error") or "").strip(),
    }


def _load_latest_strategist_run_statuses() -> dict[int, dict]:
    """Return the most recent persisted strategist status for each company.

    A failed strategist response has no manager-decision row because the cycle
    intentionally stops before manager evaluation.  The dashboard therefore
    reads the already-persisted strategist run directly to show that failure.
    """
    try:
        with get_connection(DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT
                    sr.company_id,
                    c.symbol,
                    c.name AS company_name,
                    sr.status,
                    sr.error_message,
                    sr.input_article_ids_json,
                    sr.created_at
                FROM strategist_runs AS sr
                JOIN companies AS c ON c.id = sr.company_id
                ORDER BY sr.company_id ASC, sr.created_at DESC, sr.id DESC
                """
            ).fetchall()
    except Exception:
        return {}

    latest_by_company: dict[int, dict] = {}
    for row in rows:
        item = dict(row)
        company_id = int(item.get("company_id") or 0)
        if company_id <= 0 or company_id in latest_by_company:
            continue
        article_ids = safe_json_loads(item.get("input_article_ids_json"))
        latest_by_company[company_id] = {
            "company_id": company_id,
            "symbol": str(item.get("symbol") or "").strip().upper(),
            "company_name": str(item.get("company_name") or "").strip(),
            "status": str(item.get("status") or "").strip().lower(),
            "error_message": str(item.get("error_message") or "").strip(),
            "input_article_count": len(article_ids) if isinstance(article_ids, list) else 0,
            "created_at": str(item.get("created_at") or "").strip(),
        }
    return latest_by_company


def _strategist_validation_diagnostic(status: dict | None) -> dict:
    if not isinstance(status, dict) or status.get("status") != "failed":
        return {}
    return {
        "status": "failed",
        "error_message": str(status.get("error_message") or "").strip(),
        "input_article_count": int(status.get("input_article_count") or 0),
        "created_at": str(status.get("created_at") or "").strip(),
    }


def _serialize_manager_history_row(item: dict) -> dict:
    return {
        "id": int(item.get("id") or 0),
        "decision_run_at": str(item.get("decision_run_at") or "").strip(),
        "manager_decision": str(item.get("manager_decision") or "").strip(),
        "manager_confidence": str(item.get("manager_confidence") or "").strip(),
        "manager_reason": str(item.get("manager_reason") or "").strip(),
        "target_dte_bucket": str(item.get("target_dte_bucket") or "").strip(),
        "selected_option_id": str(item.get("selected_option_id") or "").strip(),
        "selected_option_symbol": str(item.get("selected_option_symbol") or "").strip(),
        "selected_expiration_date": str(item.get("selected_expiration_date") or "").strip(),
        "selected_strike_price": safe_float(item.get("selected_strike_price")),
        "selected_option_source": str(item.get("selected_option_source") or "").strip(),
        "trade_executed": bool(item.get("trade_executed")),
        "trade_execution_order_id": str(item.get("trade_execution_order_id") or "").strip(),
        "trade_execution_record_id": item.get("trade_execution_record_id"),
        "latest_trade_pnl_pct": safe_float(item.get("latest_trade_pnl_pct")),
        "latest_trade_pnl_updated_at": str(item.get("latest_trade_pnl_updated_at") or "").strip(),
        "pnl_expires_at": str(item.get("pnl_expires_at") or "").strip(),
        "resolved_outcome_label": str(item.get("resolved_outcome_label") or "").strip(),
        "article_references": _extract_article_references_from_manager_input(
            item.get("manager_input_json"),
            limit=3,
        ),
    }


def _build_company_decisions_payload_from_history() -> dict:
    try:
        rows = list_recent_manager_decision_history(limit=250, within_days=30)
    except Exception:
        return _build_empty_company_decisions_payload()

    strategist_run_statuses = _load_latest_strategist_run_statuses()
    if not rows and not strategist_run_statuses:
        return _build_empty_company_decisions_payload()

    companies_by_symbol: dict[str, dict] = {}
    history_by_symbol: dict[str, list[dict]] = {}

    for row in rows:
        item = dict(row or {})
        item["manager_input_json"] = safe_json_loads(item.get("manager_input_json"))
        item["manager_output_json"] = safe_json_loads(item.get("manager_output_json"))
        symbol, name = _normalize_history_company_identity(item, item.get("manager_input_json"))
        if not symbol:
            continue

        history_by_symbol.setdefault(symbol, []).append(item)

        if symbol in companies_by_symbol:
            if name and not companies_by_symbol[symbol].get("name"):
                companies_by_symbol[symbol]["name"] = name
            continue

        companies_by_symbol[symbol] = _empty_company_decision_entry(
            symbol=symbol,
            name=name,
            last_updated_at=str(item.get("decision_run_at") or "").strip(),
        )
        companies_by_symbol[symbol]["strategist"] = _extract_strategist_snapshot_from_manager_input(
            item.get("manager_input_json"),
            fallback_decision=item.get("strategist_decision"),
        )
        companies_by_symbol[symbol]["manager"] = _extract_manager_snapshot_from_history_row(
            item,
            item.get("manager_output_json"),
        )
        company_id = int(item.get("company_id") or 0)
        companies_by_symbol[symbol]["diagnostics"] = {
            "historical_bars": _extract_historical_bars_diagnostic(item.get("manager_input_json")),
            "strategist_validation": _strategist_validation_diagnostic(
                strategist_run_statuses.get(company_id)
            ),
        }

    for company_id, strategist_status in strategist_run_statuses.items():
        validation = _strategist_validation_diagnostic(strategist_status)
        if not validation:
            continue
        symbol = str(strategist_status.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        if symbol not in companies_by_symbol:
            companies_by_symbol[symbol] = _empty_company_decision_entry(
                symbol=symbol,
                name=str(strategist_status.get("company_name") or "").strip(),
                last_updated_at=str(strategist_status.get("created_at") or "").strip(),
            )
        companies_by_symbol[symbol]["diagnostics"]["strategist_validation"] = validation

    for symbol, company_entry in companies_by_symbol.items():
        company_entry["manager_history"] = [
            _serialize_manager_history_row(item)
            for item in history_by_symbol.get(symbol, [])[:3]
        ]

    companies = sorted(
        companies_by_symbol.values(),
        key=lambda entry: (
            str(entry.get("last_updated_at") or ""),
            str(entry.get("symbol") or ""),
        ),
        reverse=True,
    )
    last_updated_at = str(companies[0].get("last_updated_at") or "").strip() if companies else ""
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "last_updated_at": last_updated_at,
        "company_count": len(companies),
        "companies": companies,
    }


def build_company_decisions_payload() -> dict:
    return _build_company_decisions_payload_from_history()


def build_market_regime_payload() -> dict:
    snapshot = get_latest_market_regime_snapshot()
    if not isinstance(snapshot, dict):
        return {
            "available": False,
            "state": "unknown",
            "reason_codes": ["no_market_regime_snapshot_recorded"],
            "input_freshness": {},
            "macro_event_window": {},
        }
    return {"available": True, **snapshot}


DECISION_EXPECTANCY_GROUPS = {
    "confidence",
    "dte_bucket",
    "selector_mode",
    "sector",
    "time_of_day",
    "confidence_x_dte_bucket",
    "confidence_x_selector_mode",
    "entry_gate",
    "market_regime",
    "evidence_count_bucket",
    "trend_state",
    "relative_strength_state",
    "volume_state",
}
DECISION_EXPECTANCY_BASES = {"marked", "closed"}
DECISION_EXPECTANCY_DECISIONS = {"all", "call", "put"}
DECISION_EXPECTANCY_CONFIDENCES = {"all", "high", "medium", "low"}
DECISION_EXPECTANCY_MAX_DAYS = 730


def _expectancy_text(value: object, fallback: str = "unknown") -> str:
    normalized = str(value or "").strip().lower()
    return normalized or fallback


def _normalize_selector_mode(value: object) -> str:
    normalized = _expectancy_text(value, "")
    if not normalized:
        return "unknown"
    if "fallback" in normalized or "guardrail" in normalized:
        return "fallback_or_guardrail"
    if "hybrid" in normalized:
        return "hybrid"
    if "greek" in normalized:
        return "greeks"
    if "simple" in normalized:
        return "simple"
    if "manager_only" in normalized or "manager-only" in normalized or normalized == "manager":
        return "manager_only"
    return "unknown"


def _expectancy_time_of_day_bucket(value: object) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return "unknown"
    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local_time = parsed.astimezone(ZoneInfo("America/New_York")).time()
    minute_of_day = (local_time.hour * 60) + local_time.minute
    if 4 * 60 <= minute_of_day < 9 * 60 + 30:
        return "pre_market"
    if 9 * 60 + 30 <= minute_of_day < 10 * 60 + 30:
        return "opening_hour"
    if 10 * 60 + 30 <= minute_of_day < 15 * 60:
        return "midday"
    if 15 * 60 <= minute_of_day < 16 * 60:
        return "closing_hour"
    return "after_hours"


def _expectancy_label(value: str, *, kind: str) -> str:
    if value == "unknown":
        return "Unknown"
    if kind == "dte_bucket":
        return f"{value.replace('_', '–')} DTE"
    if kind == "selector_mode":
        return _titleize_ranking_key(value.replace("_", " "))
    if kind == "time_of_day":
        return {
            "pre_market": "Pre-market ET",
            "opening_hour": "Opening hour ET",
            "midday": "Midday ET",
            "closing_hour": "Closing hour ET",
            "after_hours": "After-hours ET",
        }.get(value, _titleize_ranking_key(value))
    if kind == "entry_gate":
        return {"allowed": "Continuation allowed", "blocked": "Continuation blocked"}.get(value, _titleize_ranking_key(value))
    return _titleize_ranking_key(value)


def _is_fresh_marked_pnl(row: dict, *, reference_time: datetime) -> bool:
    if safe_float(row.get("latest_trade_pnl_pct")) is None:
        return False
    expiration = str(row.get("pnl_expires_at") or "").strip()
    if not expiration:
        return True
    try:
        expires_at = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > reference_time.astimezone(timezone.utc)


def _expectancy_pnl(row: dict, *, basis: str, reference_time: datetime) -> float | None:
    if basis == "closed":
        if _expectancy_text(row.get("outcome_status"), "") != "closed":
            return None
        return safe_float(row.get("realized_pnl_pct"))
    return safe_float(row.get("latest_trade_pnl_pct")) if _is_fresh_marked_pnl(row, reference_time=reference_time) else None


def _validate_decision_expectancy_options(
    *,
    group_by: str,
    basis: str,
    within_days: int,
    min_samples: int,
    decision: str,
    confidence: str,
) -> None:
    if group_by not in DECISION_EXPECTANCY_GROUPS:
        raise ValueError("Invalid group_by value.")
    if basis not in DECISION_EXPECTANCY_BASES:
        raise ValueError("Invalid basis value.")
    if decision not in DECISION_EXPECTANCY_DECISIONS:
        raise ValueError("Invalid decision value.")
    if confidence not in DECISION_EXPECTANCY_CONFIDENCES:
        raise ValueError("Invalid confidence value.")
    if within_days < 1 or within_days > DECISION_EXPECTANCY_MAX_DAYS:
        raise ValueError(f"within_days must be between 1 and {DECISION_EXPECTANCY_MAX_DAYS}.")
    if min_samples < 1 or min_samples > 100:
        raise ValueError("min_samples must be between 1 and 100.")


def _expectancy_group(row: dict, group_by: str) -> tuple[str, str]:
    field_labels = {
        "confidence": (row["confidence"], "confidence"),
        "dte_bucket": (row["dte_bucket"], "dte_bucket"),
        "selector_mode": (row["selector_mode"], "selector_mode"),
        "sector": (row["sector"], "sector"),
        "time_of_day": (row["time_of_day"], "time_of_day"),
        "entry_gate": (row["entry_gate"], "entry_gate"),
        "market_regime": (row["market_regime"], "generic"),
        "evidence_count_bucket": (row["evidence_count_bucket"], "generic"),
        "trend_state": (row["trend_state"], "generic"),
        "relative_strength_state": (row["relative_strength_state"], "generic"),
        "volume_state": (row["volume_state"], "generic"),
    }
    if group_by in field_labels:
        value, kind = field_labels[group_by]
        return value, _expectancy_label(value, kind=kind)
    if group_by == "confidence_x_dte_bucket":
        return (
            f"{row['confidence']}|{row['dte_bucket']}",
            f"{_expectancy_label(row['confidence'], kind='confidence')} | {_expectancy_label(row['dte_bucket'], kind='dte_bucket')}",
        )
    return (
        f"{row['confidence']}|{row['selector_mode']}",
        f"{_expectancy_label(row['confidence'], kind='confidence')} | {_expectancy_label(row['selector_mode'], kind='selector_mode')}",
    )


def build_decision_expectancy_payload(
    *,
    group_by: str = "confidence",
    basis: str = "marked",
    within_days: int = 90,
    min_samples: int = 5,
    decision: str = "all",
    confidence: str = "all",
    sector: str | None = None,
    now: datetime | None = None,
) -> dict:
    normalized_group_by = _expectancy_text(group_by)
    normalized_basis = _expectancy_text(basis)
    normalized_decision = _expectancy_text(decision, "all")
    normalized_confidence = _expectancy_text(confidence, "all")
    normalized_sector = _expectancy_text(sector) if sector else ""
    normalized_within_days = int(within_days)
    normalized_min_samples = int(min_samples)
    _validate_decision_expectancy_options(
        group_by=normalized_group_by,
        basis=normalized_basis,
        within_days=normalized_within_days,
        min_samples=normalized_min_samples,
        decision=normalized_decision,
        confidence=normalized_confidence,
    )
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    raw_rows = [dict(row) for row in list_decision_expectancy_rows(
        within_days=normalized_within_days,
        as_of=reference_time,
    )]
    normalized_rows: list[dict] = []
    for raw_row in raw_rows:
        manager_output = safe_json_loads(raw_row.get("manager_output_json"))
        manager_output = manager_output if isinstance(manager_output, dict) else {}
        entry_gate = manager_output.get("entry_gate") if isinstance(manager_output.get("entry_gate"), dict) else {}
        market_regime_gate = manager_output.get("market_regime_gate") if isinstance(manager_output.get("market_regime_gate"), dict) else {}
        gate_evidence = entry_gate.get("evidence") if isinstance(entry_gate.get("evidence"), dict) else {}
        gate_technical = entry_gate.get("technical") if isinstance(entry_gate.get("technical"), dict) else {}
        citation_count = int(gate_evidence.get("citation_count") or 0)
        row = {
            **raw_row,
            "decision": _expectancy_text(raw_row.get("manager_decision")),
            "confidence": _expectancy_text(raw_row.get("manager_confidence")),
            "dte_bucket": _expectancy_text(raw_row.get("target_dte_bucket")),
            "selector_mode": _normalize_selector_mode(raw_row.get("selected_option_source")),
            "sector": _expectancy_text(raw_row.get("sector_key")),
            "time_of_day": _expectancy_time_of_day_bucket(raw_row.get("decision_run_at")),
            "entry_gate": "allowed" if entry_gate.get("allowed") is True else "blocked" if entry_gate.get("allowed") is False else "unknown",
            "market_regime": _expectancy_text(market_regime_gate.get("regime_state")),
            "evidence_count_bucket": "3_plus" if citation_count >= 3 else "2" if citation_count == 2 else "0_1" if citation_count else "unknown",
            "trend_state": _expectancy_text(gate_technical.get("trend_state")),
            "relative_strength_state": _expectancy_text(gate_technical.get("relative_strength_confirmation")),
            "volume_state": _expectancy_text(gate_technical.get("volume_confirmation")),
            "pnl_pct": _expectancy_pnl(raw_row, basis=normalized_basis, reference_time=reference_time),
        }
        if normalized_decision != "all" and row["decision"] != normalized_decision:
            continue
        if normalized_confidence != "all" and row["confidence"] != normalized_confidence:
            continue
        if normalized_sector and row["sector"] != normalized_sector:
            continue
        normalized_rows.append(row)

    cohorts: dict[str, dict] = {}
    for row in normalized_rows:
        key, label = _expectancy_group(row, normalized_group_by)
        cohort = cohorts.setdefault(key, {"key": key, "label": label, "rows": []})
        cohort["rows"].append(row)

    serialized_cohorts: list[dict] = []
    for cohort in cohorts.values():
        cohort_rows = cohort["rows"]
        pnl_values = [float(row["pnl_pct"]) for row in cohort_rows if row["pnl_pct"] is not None]
        wins = [value for value in pnl_values if value > 0]
        losses = [value for value in pnl_values if value < 0]
        flats = [value for value in pnl_values if value == 0]
        gross_loss = abs(sum(losses))
        serialized_cohorts.append(
            {
                "key": cohort["key"],
                "label": cohort["label"],
                "trade_count": len(cohort_rows),
                "pnl_sample_count": len(pnl_values),
                "unresolved_count": len(cohort_rows) - len(pnl_values),
                "win_count": len(wins),
                "loss_count": len(losses),
                "flat_count": len(flats),
                "win_rate_pct": round((len(wins) / len(pnl_values)) * 100, 2) if pnl_values else None,
                "avg_pnl_pct": round(sum(pnl_values) / len(pnl_values), 2) if pnl_values else None,
                "median_pnl_pct": round(float(median(pnl_values)), 2) if pnl_values else None,
                "avg_win_pct": round(sum(wins) / len(wins), 2) if wins else None,
                "avg_loss_pct": round(sum(losses) / len(losses), 2) if losses else None,
                "expectancy_pct": round(sum(pnl_values) / len(pnl_values), 2) if pnl_values else None,
                "profit_factor_like": round(sum(wins) / gross_loss, 2) if gross_loss else None,
                "insufficient_sample": len(pnl_values) < normalized_min_samples,
                "latest_decision_at": max(str(row.get("decision_run_at") or "") for row in cohort_rows),
            }
        )
    serialized_cohorts.sort(key=lambda item: (item["avg_pnl_pct"] is None, -(item["avg_pnl_pct"] or 0), item["label"]))
    sample_count = sum(1 for row in normalized_rows if row["pnl_pct"] is not None)
    return {
        "as_of": reference_time.isoformat(),
        "basis": normalized_basis,
        "basis_label": "Marked P/L, not realized P/L" if normalized_basis == "marked" else "Closed realized P/L",
        "filters": {
            "group_by": normalized_group_by,
            "within_days": normalized_within_days,
            "min_samples": normalized_min_samples,
            "decision": normalized_decision,
            "confidence": normalized_confidence,
            "sector": normalized_sector or None,
        },
        "summary": {
            "eligible_trade_count": len(normalized_rows),
            "pnl_sample_count": sample_count,
            "unresolved_count": len(normalized_rows) - sample_count,
        },
        "cohorts": serialized_cohorts,
        "read_only": True,
        "no_trade_executed_by_feature": True,
    }


def _titleize_ranking_key(value: str) -> str:
    normalized = str(value or "").strip().replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in normalized.split())


def _build_top_rankings_payload() -> dict:
    try:
        rankings = get_current_rankings(top_sector_count=3, top_industry_count=3)
    except Exception:
        return {
            "top_sectors": [],
        }

    top_industries_by_sector = rankings.get("top_industries_by_sector", {})
    top_sectors = []
    for entry in rankings.get("top_sectors", []):
        if not isinstance(entry, dict):
            continue
        sector_key = str(entry.get("sector_key") or "").strip()
        if not sector_key:
            continue
        ranked_industries = []
        if isinstance(top_industries_by_sector, dict):
            for industry_entry in top_industries_by_sector.get(sector_key, []):
                if not isinstance(industry_entry, dict):
                    continue
                industry_key = str(industry_entry.get("industry_key") or "").strip()
                if not industry_key:
                    continue
                ranked_industries.append(
                    {
                        "industry_key": industry_key,
                        "label": _titleize_ranking_key(industry_key),
                        "score": safe_float(industry_entry.get("score")),
                    }
                )
        top_sectors.append(
            {
                "sector_key": sector_key,
                "label": _titleize_ranking_key(sector_key),
                "score": safe_float(entry.get("score")),
                "top_industries": ranked_industries[:3],
            }
        )

    return {
        "top_sectors": top_sectors[:3],
    }


def _build_usage_summary() -> dict:
    try:
        summary = get_telemetry_summary(project="Stock-trading-experiment")
    except Exception:
        return {
            "unique_install_count": 0,
            "startup_ping_count": 0,
            "last_seen_at": "",
        }

    return {
        "unique_install_count": int(summary.get("unique_install_count") or 0),
        "startup_ping_count": int(summary.get("startup_ping_count") or 0),
        "last_seen_at": str(summary.get("last_seen_at") or "").strip(),
    }


def looks_like_option_symbol(symbol: str, asset_class: str = "") -> bool:
    normalized_asset_class = str(asset_class or "").strip().lower()
    if "option" in normalized_asset_class:
        return True
    return bool(re.fullmatch(r"[A-Z]+\d{6}[CP]\d{8}", str(symbol or "").strip().upper()))


def parse_option_symbol(symbol: str) -> dict[str, object]:
    normalized_symbol = str(symbol or "").strip().upper()
    match = re.fullmatch(r"([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d{8})", normalized_symbol)
    if not match:
        return {
            "underlying_symbol": normalized_symbol,
            "contract_type": "",
            "expiration_date": "",
            "strike": None,
        }

    underlying, year, month, day, contract_type, strike_text = match.groups()
    strike = int(strike_text) / 1000.0
    return {
        "underlying_symbol": underlying,
        "contract_type": "call" if contract_type == "C" else "put",
        "expiration_date": f"20{year}-{month}-{day}",
        "strike": strike,
    }


def compute_max_drawdown_pct(portfolio_history: dict) -> float | None:
    equity_series = portfolio_history.get("equity")
    if not isinstance(equity_series, list) or not equity_series:
        return None

    peak = None
    max_drawdown_pct = 0.0
    for raw_value in equity_series:
        value = safe_float(raw_value)
        if value is None:
            continue
        if peak is None or value > peak:
            peak = value
            continue
        if peak and peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, ((peak - value) / peak) * 100.0)

    return round(max_drawdown_pct, 2)


def compute_days_to_expiration(expiration_date_text: str | None) -> float | None:
    if not expiration_date_text:
        return None

    parsed_expiration = parse_datetime(str(expiration_date_text))
    if parsed_expiration is None:
        return None

    expiration_date = parsed_expiration.date()
    current_date = datetime.now(timezone.utc).date()
    return float((expiration_date - current_date).days)


def compute_win_rate_from_fills(fills) -> dict:
    if not isinstance(fills, list):
        return {"wins": 0, "closed_trades": 0, "win_rate_pct": None}

    ordered_fills = sorted(
        fills,
        key=lambda fill: parse_datetime(str(fill.get("transaction_time") or "")) or datetime.min.replace(tzinfo=timezone.utc),
    )

    inventory: dict[str, dict[str, float]] = {}
    wins = 0
    closed_trades = 0

    for fill in ordered_fills:
        symbol = str(fill.get("symbol") or "").strip().upper()
        side = str(fill.get("side") or fill.get("order_side") or "").strip().lower()
        qty = safe_float(fill.get("qty"))
        price = safe_float(fill.get("price"))
        if not symbol or side not in {"buy", "sell"} or qty is None or qty <= 0 or price is None:
            continue

        position = inventory.setdefault(symbol, {"qty": 0.0, "avg_cost": 0.0})
        if side == "buy":
            total_cost = (position["qty"] * position["avg_cost"]) + (qty * price)
            position["qty"] += qty
            if position["qty"] > 0:
                position["avg_cost"] = total_cost / position["qty"]
            continue

        if position["qty"] <= 0:
            continue

        closed_qty = min(position["qty"], qty)
        realized_pl = (price - position["avg_cost"]) * closed_qty
        closed_trades += 1
        if realized_pl > 0:
            wins += 1

        position["qty"] = max(0.0, position["qty"] - qty)
        if position["qty"] == 0:
            position["avg_cost"] = 0.0

    win_rate_pct = round((wins / closed_trades) * 100.0, 1) if closed_trades else None
    return {
        "wins": wins,
        "closed_trades": closed_trades,
        "win_rate_pct": win_rate_pct,
    }


def summarize_market_status(clock_payload: dict) -> dict:
    is_open = bool(clock_payload.get("is_open", False))
    next_open = str(clock_payload.get("next_open") or "")
    next_close = str(clock_payload.get("next_close") or "")
    timestamp = str(clock_payload.get("timestamp") or "")
    return {
        "state": "open" if is_open else "closed",
        "label": "Open" if is_open else "Closed",
        "detail": f"Next close: {next_close}" if is_open and next_close else f"Next open: {next_open}" if next_open else "",
        "timestamp": timestamp,
        "next_open": next_open,
        "next_close": next_close,
    }


def status_snapshot(name: str, payload) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {"name": name, "state": "down", "label": "Down", "updated_at": ""}

    updated_at = str(payload.get("updated_at") or "")
    updated_at_dt = parse_datetime(updated_at)
    is_fresh = False
    if updated_at_dt is not None:
        is_fresh = (
            datetime.now(timezone.utc) - updated_at_dt.astimezone(timezone.utc)
        ).total_seconds() <= BOT_DOWN_THRESHOLD_SECONDS

    raw_state = str(payload.get("state") or "").strip().lower()
    state = raw_state if raw_state in {"starting", "running", "paused", "error"} else "unknown"
    if not is_fresh:
        state = "down"

    label = state.replace("_", " ").title()
    return {
        "name": name,
        "state": state,
        "label": label,
        "updated_at": updated_at,
    }


def summarize_bot_status(worker_status, option_manager_status) -> dict:
    components = []
    worker_snapshot = status_snapshot("Worker", worker_status)
    if worker_snapshot.get("updated_at") or worker_snapshot.get("state") != "down":
        components.append(worker_snapshot)

    option_snapshot = status_snapshot("Option Manager", option_manager_status)
    if option_snapshot.get("updated_at") or option_snapshot.get("state") != "down":
        components.append(option_snapshot)

    if not components:
        components = [worker_snapshot]

    states = {component["state"] for component in components}
    if states == {"running"}:
        overall_state = "healthy"
        label = "Healthy"
    elif states & {"running", "paused", "starting"}:
        overall_state = "degraded"
        label = "Degraded"
    else:
        overall_state = "down"
        label = "Down"

    detail = ", ".join(
        f"{component['name']} {component['label'].lower()}" for component in components
    )
    return {
        "state": overall_state,
        "label": label,
        "detail": detail,
        "components": components,
    }


def load_option_management_snapshot() -> tuple[dict[str, dict], dict]:
    payload = read_json_payload(OPTION_POSITION_MANAGEMENT_OUTPUT_PATH)
    if not isinstance(payload, dict):
        return {}, {}

    positions = payload.get("positions")
    if not isinstance(positions, list):
        return {}, payload

    by_symbol: dict[str, dict] = {}
    for entry in positions:
        if not isinstance(entry, dict):
            continue
        symbol = str(entry.get("symbol") or "").strip().upper()
        if symbol:
            by_symbol[symbol] = entry

    return by_symbol, payload


def extract_option_quote_fields(quote_payload: dict) -> dict[str, float | str | None]:
    if not isinstance(quote_payload, dict):
        return {"bid_price": None, "ask_price": None, "timestamp": ""}

    bid_price = safe_float(
        quote_payload.get("bid_price")
        or quote_payload.get("bp")
    )
    ask_price = safe_float(
        quote_payload.get("ask_price")
        or quote_payload.get("ap")
    )
    timestamp = str(
        quote_payload.get("timestamp")
        or quote_payload.get("t")
        or ""
    )
    return {
        "bid_price": bid_price,
        "ask_price": ask_price,
        "timestamp": timestamp,
    }


def get_latest_option_quote(symbol: str, quote_cache: dict[str, dict]) -> dict[str, float | str | None]:
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        return {"bid_price": None, "ask_price": None, "timestamp": ""}

    if normalized_symbol in quote_cache:
        return quote_cache[normalized_symbol]

    fallback = {"bid_price": None, "ask_price": None, "timestamp": ""}
    try:
        payload = alpaca_data_get_json(
            "/v1beta1/options/quotes/latest",
            {"symbols": normalized_symbol},
        )
    except Exception:
        quote_cache[normalized_symbol] = fallback
        return fallback

    quotes = payload.get("quotes") if isinstance(payload, dict) else None
    if not isinstance(quotes, dict):
        quote_cache[normalized_symbol] = fallback
        return fallback

    quote = quotes.get(normalized_symbol)
    extracted = extract_option_quote_fields(quote)
    quote_cache[normalized_symbol] = extracted
    return extracted


def compute_option_unrealized_pl_pct(
    entry_price: float | None,
    current_mark_price: float | None,
    fallback_unrealized_pl_pct: float | None,
) -> float | None:
    if entry_price is not None and entry_price > 0 and current_mark_price is not None:
        return round(((current_mark_price - entry_price) / entry_price) * 100.0, 4)
    return fallback_unrealized_pl_pct


def resolve_option_mark_price(
    *,
    raw_position: dict,
    current_bid: float | None,
    current_ask: float | None,
    snapshot: dict,
) -> float | None:
    broker_mark_price = safe_float(raw_position.get("current_price"))
    if broker_mark_price is not None:
        return broker_mark_price
    if current_bid is not None:
        return current_bid
    if current_bid is not None and current_ask is not None:
        return round((current_bid + current_ask) / 2.0, 4)
    snapshot_mark_price = safe_float(snapshot.get("mark_price"))
    if snapshot_mark_price is not None:
        return snapshot_mark_price
    return safe_float(snapshot.get("mid_price"))


def format_exit_rule_status(position_summary: dict, management_payload: dict) -> str:
    reasons = position_summary.get("decision_reasons")
    normalized_reasons = reasons if isinstance(reasons, list) else []
    reason_text = " ".join(str(reason) for reason in normalized_reasons).lower()
    decision = str(position_summary.get("decision") or "").strip().lower()

    if "stop-loss" in reason_text:
        return "Stop loss triggered" if decision == "sell" else "Near stop loss"
    if "take-profit" in reason_text:
        return "Take profit triggered" if decision == "sell" else "Near take profit"
    if "expiration" in reason_text or "hours to expiration" in reason_text:
        return "Near expiration"

    unrealized_pl_pct = safe_float(position_summary.get("unrealized_pl_pct"))
    stop_loss_pct = safe_float(position_summary.get("stop_loss_pct"))
    if stop_loss_pct is None:
        stop_loss_pct = safe_float(management_payload.get("stop_loss_pct"))
    take_profit_pct = safe_float(position_summary.get("take_profit_pct"))
    if take_profit_pct is None:
        take_profit_pct = safe_float(management_payload.get("take_profit_pct"))
    hours_to_expiration = safe_float(position_summary.get("hours_to_expiration"))
    exit_hours = safe_float(position_summary.get("exit_hours_to_expiration"))
    if exit_hours is None:
        exit_hours = safe_float(management_payload.get("exit_hours_to_expiration"))

    if (
        unrealized_pl_pct is not None
        and stop_loss_pct is not None
        and stop_loss_pct < 0
        and unrealized_pl_pct <= stop_loss_pct * 0.75
    ):
        return "Near stop loss"
    if (
        unrealized_pl_pct is not None
        and take_profit_pct is not None
        and take_profit_pct > 0
        and unrealized_pl_pct >= take_profit_pct * 0.75
    ):
        return "Near take profit"
    if (
        hours_to_expiration is not None
        and exit_hours is not None
        and hours_to_expiration <= exit_hours * 1.5
    ):
        return "Near expiration"

    return "Monitoring"


def _is_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _price_at_profit_pct(entry_price: float | None, profit_pct: float | None) -> float | None:
    if entry_price is None or profit_pct is None:
        return None
    return round(entry_price * (1.0 + (profit_pct / 100.0)), 4)


def build_option_protection_payload(
    *,
    snapshot: dict,
    management_payload: dict,
    entry_price: float | None,
) -> dict:
    """Return the manager's active price-exit floor and its recorded high-water mark.

    The manager persists trailing values as decimal P/L ratios (0.40 means 40%).
    The dashboard API exposes human-readable percentages and estimated option marks
    derived from the entry price, while making the source of the active floor explicit.
    """
    protected_floor_ratio = safe_float(snapshot.get("protected_profit_floor_pct"))
    protection_active = _is_enabled(snapshot.get("profit_protection_active"))
    stop_loss_pct = safe_float(snapshot.get("stop_loss_pct"))
    if stop_loss_pct is None:
        stop_loss_pct = safe_float(management_payload.get("stop_loss_pct"))

    if protection_active and protected_floor_ratio is not None:
        floor_pct = round(protected_floor_ratio * 100.0, 4)
        floor_type = "trailing_profit"
    elif stop_loss_pct is not None:
        floor_pct = stop_loss_pct
        floor_type = "stop_loss"
    else:
        floor_pct = None
        floor_type = "unavailable"

    high_water_ratio = safe_float(snapshot.get("max_pnl_pct"))
    high_water_pct = round(high_water_ratio * 100.0, 4) if high_water_ratio is not None else None

    return {
        "floor_type": floor_type,
        "floor_profit_pct": floor_pct,
        "floor_mark_price": _price_at_profit_pct(entry_price, floor_pct),
        "trailing_profit_armed": protection_active and protected_floor_ratio is not None,
        "high_water_profit_pct": high_water_pct,
        "high_water_mark_price": _price_at_profit_pct(entry_price, high_water_pct),
    }


def build_open_positions_payload() -> dict:
    live_positions = alpaca_get_json("/v2/positions")
    normalized_positions = live_positions if isinstance(live_positions, list) else []
    option_snapshots, management_payload = load_option_management_snapshot()
    option_quote_cache: dict[str, dict] = {}

    rows: list[dict] = []
    option_count = 0
    stock_count = 0

    for raw_position in normalized_positions:
        if not isinstance(raw_position, dict):
            continue

        symbol = str(raw_position.get("symbol") or "").strip().upper()
        asset_class = str(raw_position.get("asset_class") or "")
        is_option = looks_like_option_symbol(symbol, asset_class)
        snapshot = option_snapshots.get(symbol, {})
        parsed_option = parse_option_symbol(symbol) if is_option else {}

        quantity = safe_float(snapshot.get("quantity")) if is_option else None
        if quantity is None:
            quantity = safe_float(raw_position.get("qty"))

        entry_price = safe_float(snapshot.get("entry_price")) if is_option else None
        if entry_price is None:
            entry_price = safe_float(raw_position.get("avg_entry_price"))

        current_bid = safe_float(snapshot.get("current_bid")) if is_option else None
        current_ask = safe_float(snapshot.get("current_ask")) if is_option else None
        if is_option and (current_bid is None or current_ask is None):
            latest_quote = get_latest_option_quote(symbol, option_quote_cache)
            if current_bid is None:
                current_bid = safe_float(latest_quote.get("bid_price"))
            if current_ask is None:
                current_ask = safe_float(latest_quote.get("ask_price"))
        mid_price = None
        if is_option and current_bid is not None and current_ask is not None:
            mid_price = round((current_bid + current_ask) / 2.0, 4)
        elif is_option:
            mid_price = safe_float(snapshot.get("mid_price"))
        if mid_price is None:
            mid_price = safe_float(raw_position.get("current_price"))

        mark_price = (
            resolve_option_mark_price(
                raw_position=raw_position,
                current_bid=current_bid,
                current_ask=current_ask,
                snapshot=snapshot,
            )
            if is_option
            else mid_price
        )

        unrealized_pl_pct = safe_float(snapshot.get("unrealized_pl_pct")) if is_option else None
        if unrealized_pl_pct is None:
            live_unrealized = safe_float(raw_position.get("unrealized_plpc"))
            unrealized_pl_pct = round(live_unrealized * 100.0, 4) if live_unrealized is not None else None
        if is_option:
            unrealized_pl_pct = compute_option_unrealized_pl_pct(
                entry_price,
                mark_price,
                unrealized_pl_pct,
            )

        expiration_text = str(snapshot.get("expiration_date") or parsed_option.get("expiration_date") or "")
        days_to_expiration = safe_float(snapshot.get("days_to_expiration"))
        if days_to_expiration is None and is_option:
            days_to_expiration = compute_days_to_expiration(expiration_text)

        if is_option:
            option_count += 1
            contract_type = str(snapshot.get("contract_type") or parsed_option.get("contract_type") or "").strip().lower()
            rows.append(
                {
                    "symbol": str(snapshot.get("underlying_symbol") or parsed_option.get("underlying_symbol") or symbol),
                    "contract_symbol": symbol,
                    "position_kind": "option",
                    "type": contract_type.title() if contract_type else "Option",
                    "strike": safe_float(snapshot.get("strike")) if snapshot else parsed_option.get("strike"),
                    "expiration": expiration_text,
                    "quantity": quantity,
                    "entry_price": entry_price,
                    "current_bid": current_bid,
                    "current_ask": current_ask,
                    "mark_price": mark_price,
                    "mid_price": mid_price,
                    "unrealized_pl_pct": unrealized_pl_pct,
                    "days_to_expiration": days_to_expiration,
                    "profit_protection": build_option_protection_payload(
                        snapshot=snapshot,
                        management_payload=management_payload,
                        entry_price=entry_price,
                    ),
                    "exit_rule_status": format_exit_rule_status(snapshot, management_payload),
                    "decision": str(snapshot.get("decision") or ""),
                    "decision_reasons": snapshot.get("decision_reasons") if isinstance(snapshot.get("decision_reasons"), list) else [],
                }
            )
            continue

        stock_count += 1
        rows.append(
            {
                "symbol": symbol,
                "contract_symbol": symbol,
                "position_kind": "stock",
                "type": "Stock",
                "strike": None,
                "expiration": "",
                "quantity": quantity,
                "entry_price": entry_price,
                "current_bid": None,
                "current_ask": None,
                "mark_price": mid_price,
                "mid_price": mid_price,
                "unrealized_pl_pct": unrealized_pl_pct,
                "days_to_expiration": None,
                "profit_protection": None,
                "exit_rule_status": "N/A",
                "decision": "",
                "decision_reasons": [],
            }
        )

    rows.sort(
        key=lambda row: (
            0 if row.get("position_kind") == "option" else 1,
            str(row.get("expiration") or "9999-99-99"),
            str(row.get("symbol") or ""),
        )
    )

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "position_count": len(rows),
        "option_count": option_count,
        "stock_count": stock_count,
        "positions": rows,
    }


def build_risk_controls_payload() -> dict:
    fallback_take_profit_pct = OPTION_POSITION_SETTINGS.take_profit_pct
    fallback_stop_loss_pct = OPTION_POSITION_SETTINGS.stop_loss_pct
    expiration_exit_hours = OPTION_POSITION_SETTINGS.exit_hours_to_expiration
    dte_rule_configs = load_dte_exit_rule_configs()
    take_profit_rule_summary = ", ".join(
        f"{rule['label']}: +{rule['take_profit_pct']:.0f}%"
        for rule in dte_rule_configs
    )
    stop_loss_rule_summary = ", ".join(
        f"{rule['label']}: {rule['stop_loss_pct']:.0f}%"
        for rule in dte_rule_configs
    )
    expiration_rule_summary = ", ".join(
        f"{rule['label']}: <={rule['force_exit_days_to_expiration']} DTE"
        for rule in dte_rule_configs
    )
    fallback_threshold_detail = (
        "Positions outside those DTE buckets fall back to the legacy defaults "
        f"({fallback_take_profit_pct:.0f}% TP / {fallback_stop_loss_pct:.0f}% SL / {expiration_exit_hours:.0f}h expiration exit)."
        if fallback_take_profit_pct is not None
        and fallback_stop_loss_pct is not None
        and expiration_exit_hours is not None
        else "Positions outside those DTE buckets fall back to the legacy default thresholds when configured."
    )
    per_trade_buying_power_cap_pct = PER_ORDER_SIZING_BUYING_POWER_PCT
    total_options_exposure_cap_pct = MAX_DEPLOYABLE_BUYING_POWER_PCT
    effective_per_trade_cap_pct = round(
        (MAX_DEPLOYABLE_BUYING_POWER_PCT * PER_ORDER_SIZING_BUYING_POWER_PCT) / 100.0,
        2,
    )

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "controls": [
            {
                "label": "Max risk per trade",
                "value": (
                    f"{per_trade_buying_power_cap_pct:.0f}% of deployable buying power "
                    f"(~{effective_per_trade_cap_pct:.2f}% of total buying power)"
                ),
                "detail": (
                    "Current order sizing uses the configured share of deployable buying power for a single option idea, "
                    "subject to contract-price math and the AGENT_OPTION_ORDER_QTY baseline."
                ),
                "status": "configured",
            },
            {
                "label": "Max open contracts",
                "value": f"{MAX_CONCURRENT_OPTION_POSITIONS} open positions; base qty {DEFAULT_OPTION_ORDER_QTY}, up to x{MAX_OPTION_ORDER_QTY_MULTIPLIER} per idea",
                "detail": (
                    "New entries are rejected once the broker reports the configured portfolio-wide option-position count. "
                    "Each accepted idea remains subject to the configured per-idea quantity multiplier."
                ),
                "status": "configured",
            },
            {
                "label": "Max total options exposure",
                "value": f"{total_options_exposure_cap_pct:.0f}% of account buying power",
                "detail": (
                    "The execution loop stops adding new option orders once the configured "
                    "deployable buying power allowance is consumed."
                ),
                "status": "configured",
            },
            {
                "label": "Sector concentration cap",
                "value": f"{MAX_OPTION_SECTOR_EXPOSURE_PCT:.0f}% of deployable option buying power",
                "detail": "New entries are rejected when their persisted company sector would exceed this share of the total option-notional cap.",
                "status": "configured",
            },
            {
                "label": "Daily realized-loss limit",
                "value": f"${DAILY_OPTION_LOSS_LIMIT_DOLLARS:,.0f}",
                "detail": "New entries are rejected after persisted broker-confirmed option losses for the current market day reach this limit.",
                "status": "configured",
            },
            {
                "label": "Stop-loss rule",
                "value": stop_loss_rule_summary,
                "detail": (
                    "Open option positions use DTE-specific stop-loss floors. "
                    + fallback_threshold_detail
                ),
                "status": "configured",
            },
            {
                "label": "Take-profit rule",
                "value": take_profit_rule_summary,
                "detail": (
                    "Open option positions use DTE-specific take-profit targets. "
                    + fallback_threshold_detail
                ),
                "status": "configured",
            },
            {
                "label": "Expiration exit rule",
                "value": expiration_rule_summary,
                "detail": (
                    "The option manager uses DTE-specific forced exits before expiration. "
                    + fallback_threshold_detail
                ),
                "status": "configured",
            },
        ],
    }


def build_dashboard_kpis() -> dict:
    account = alpaca_get_json("/v2/account")
    positions = alpaca_get_json("/v2/positions")
    clock = alpaca_get_json("/v2/clock")
    portfolio_history = fetch_portfolio_history()

    try:
        fills = alpaca_get_json(
            "/v2/account/activities/FILL",
            {"direction": "desc", "page_size": "100"},
        )
    except Exception:
        fills = []

    equity = safe_float(account.get("equity"))
    buying_power = safe_float(account.get("buying_power"))
    last_equity = safe_float(account.get("last_equity"))
    day_pl = None
    day_pl_pct = None
    if equity is not None and last_equity not in (None, 0):
        day_pl = round(equity - last_equity, 2)
        day_pl_pct = round((day_pl / last_equity) * 100.0, 2)

    normalized_positions = positions if isinstance(positions, list) else []
    option_positions = [
        position
        for position in normalized_positions
        if looks_like_option_symbol(
            str(position.get("symbol") or ""),
            str(position.get("asset_class") or ""),
        )
    ]
    option_exposure = sum(
        abs(safe_float(position.get("market_value")) or 0.0)
        for position in option_positions
    )
    option_exposure_pct = round((option_exposure / equity) * 100.0, 2) if equity not in (None, 0) else None
    dte_rule_configs = load_dte_exit_rule_configs()
    win_rate = compute_win_rate_from_fills(fills)
    equity_curve = normalize_equity_curve(portfolio_history)
    daily_returns = compute_daily_returns(equity_curve)
    total_return_pct = compute_total_return_pct(equity_curve)
    sharpe_metrics = compute_sharpe_metrics(
        daily_returns,
        annual_risk_free_rate=DEFAULT_ANNUAL_RISK_FREE_RATE,
    )
    sortino_metrics = compute_sortino_metrics(
        daily_returns,
        annual_risk_free_rate=DEFAULT_ANNUAL_RISK_FREE_RATE,
    )
    trade_performance = compute_trade_metrics(fills)
    worker_status = read_json_payload(SCRIPT_STATUS_PATH)
    option_manager_status = read_json_payload(OPTION_MANAGER_STATUS_PATH)
    top_rankings = _build_top_rankings_payload()
    usage_summary = _build_usage_summary()
    market_regime = build_market_regime_payload()

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "account_equity": equity,
        "buying_power": buying_power,
        "day_pl": day_pl,
        "day_pl_pct": day_pl_pct,
        "open_positions": len(normalized_positions),
        "options_exposure": {
            "market_value": round(option_exposure, 2),
            "equity_pct": option_exposure_pct,
            "position_count": len(option_positions),
        },
        "option_exit_rules": {
            "take_profit_summary": " / ".join(
                f"{rule['label']}: +{rule['take_profit_pct']:.0f}%"
                for rule in dte_rule_configs
            ),
            "stop_loss_summary": " / ".join(
                f"{rule['label']}: {rule['stop_loss_pct']:.0f}%"
                for rule in dte_rule_configs
            ),
            "fallback_take_profit_pct": OPTION_POSITION_SETTINGS.take_profit_pct,
            "fallback_stop_loss_pct": OPTION_POSITION_SETTINGS.stop_loss_pct,
        },
        "win_rate": win_rate,
        "max_drawdown_pct": compute_max_drawdown_pct(portfolio_history),
        "performance_summary": {
            "total_return_pct": round(total_return_pct, 2) if total_return_pct is not None else None,
            "annualized_sharpe": round(sharpe_metrics["annualized_sharpe"], 4)
            if sharpe_metrics["annualized_sharpe"] is not None
            else None,
            "annualized_sortino": round(sortino_metrics["annualized_sortino"], 4)
            if sortino_metrics["annualized_sortino"] is not None
            else None,
            "profit_factor": trade_performance.get("profit_factor"),
            "average_trade_return_pct": trade_performance.get("average_trade_return_pct"),
        },
        "usage_summary": usage_summary,
        "bot_status": summarize_bot_status(worker_status, option_manager_status),
        "market_status": summarize_market_status(clock),
        "market_regime": market_regime,
        "top_rankings": top_rankings,
    }
