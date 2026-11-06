"""Decision expectancy: realised and marked P/L of past decisions, grouped for the dashboard."""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from zoneinfo import ZoneInfo
from market_db_modules.queries import list_decision_expectancy_rows
from api_support.request_utils import safe_json_loads
from services.core.numbers import safe_float


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
    "ticker",
    "industry",
    "exit_reason",
    "catalyst_family",
    "holding_period",
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


def _holding_period_bucket(decided_at: object, closed_at: object) -> str:
    """Decision to close; a decision still open has no holding period yet."""
    if not closed_at:
        return "open"
    try:
        start, end = (datetime.fromisoformat(str(value).replace("Z", "+00:00")) for value in (decided_at, closed_at))
    except ValueError:
        return "unknown"
    if (start.tzinfo is None) != (end.tzinfo is None):
        return "unknown"
    days = (end - start).total_seconds() / 86400
    if days < 1:
        return "under_1d"
    if days < 3:
        return "1_3d"
    if days < 7:
        return "3_7d"
    return "7d_plus"


def _expectancy_label(value: str, *, kind: str) -> str:
    if value == "unknown":
        return "Unknown"
    if kind == "dte_bucket":
        return f"{value.replace('_', '–')} DTE"
    if kind == "selector_mode":
        return titleize_key(value.replace("_", " "))
    if kind == "time_of_day":
        return {
            "pre_market": "Pre-market ET",
            "opening_hour": "Opening hour ET",
            "midday": "Midday ET",
            "closing_hour": "Closing hour ET",
            "after_hours": "After-hours ET",
        }.get(value, titleize_key(value))
    if kind == "holding_period":
        return {"under_1d": "Under 1 day", "1_3d": "1–3 days", "3_7d": "3–7 days", "7d_plus": "7+ days",
                "open": "Still open"}.get(value, titleize_key(value))
    if kind == "ticker":
        return value.upper()
    if kind == "entry_gate":
        return {"allowed": "Continuation allowed", "blocked": "Continuation blocked"}.get(value, titleize_key(value))
    return titleize_key(value)


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
        "ticker": (row["ticker"], "ticker"),
        "industry": (row["industry"], "generic"),
        "exit_reason": (row["exit_reason"], "generic"),
        "catalyst_family": (row["catalyst_family"], "generic"),
        "holding_period": (row["holding_period"], "holding_period"),
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
            "ticker": _expectancy_text(raw_row.get("symbol")),
            "industry": _expectancy_text(raw_row.get("industry_key")),
            "exit_reason": _expectancy_text(raw_row.get("primary_exit_reason")),
            "catalyst_family": _expectancy_text(raw_row.get("catalyst_family")),
            "holding_period": _holding_period_bucket(raw_row.get("decision_run_at"), raw_row.get("closed_at")),
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


def titleize_key(value: str) -> str:
    normalized = str(value or "").strip().replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in normalized.split())
