from __future__ import annotations

"""Read-side payloads for the LLM thesis validator (shadow-mode observability).

Serves the dashboard two views of the validator's output:
- the recent run history plus the promotion health metrics (flip rate,
  action rate, grounding rate) for /api/thesis-validator, and
- a per-option-symbol map of the latest verdict, merged into open positions.
"""

import os
from datetime import datetime, timezone

from market_db_modules.thesis_validator import (
    get_latest_thesis_validation_runs_by_option_symbol,
    list_thesis_validation_runs,
    summarize_thesis_validator_health,
)

from api_support.common import safe_float
from api_support.context import logger

DEFAULT_RUN_LIMIT = 50
DEFAULT_HEALTH_WINDOW_DAYS = 60

# Fields safe to send to the browser; excludes the evidence pack and raw model
# samples, which are audit data and can run to tens of kilobytes per run.
_RUN_SUMMARY_FIELDS = (
    "thesis_id",
    "option_symbol",
    "symbol",
    "run_at",
    "run_date",
    "mode",
    "sample_count",
    "consistency",
    "thesis_state",
    "would_open_today",
    "rationale",
    "rules_verdict",
    "arbiter_action",
    "arbiter_reason",
    "error_text",
)


def _summarize_run(run: dict) -> dict:
    summary = {field: run.get(field) for field in _RUN_SUMMARY_FIELDS}
    summary["flip_detected"] = bool(run.get("flip_detected"))
    summary["grounded"] = bool(run.get("grounded"))
    summary["executed"] = bool(run.get("executed"))
    summary["confidence"] = safe_float(run.get("confidence"))
    summary["mark_mid_price"] = safe_float(run.get("mark_mid_price"))
    summary["evidence_ids"] = run.get("evidence_ids") if isinstance(run.get("evidence_ids"), list) else []
    return summary


def build_thesis_validator_payload(
    *,
    limit: int = DEFAULT_RUN_LIMIT,
    health_window_days: int = DEFAULT_HEALTH_WINDOW_DAYS,
) -> dict:
    runs = list_thesis_validation_runs(limit=limit)
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "mode": str(os.getenv("THESIS_VALIDATOR_MODE", "shadow")).strip().lower() or "shadow",
        "kill_switch": str(os.getenv("THESIS_VALIDATOR_KILL_SWITCH", "")).strip().lower()
        in {"1", "true", "yes", "on"},
        "health": summarize_thesis_validator_health(max_age_days=health_window_days),
        "run_count": len(runs),
        "runs": [_summarize_run(run) for run in runs],
    }


def load_latest_thesis_verdicts() -> dict[str, dict]:
    """Latest verdict per option symbol; empty on any failure so the
    open-positions table never breaks over validator data."""
    try:
        latest_runs = get_latest_thesis_validation_runs_by_option_symbol()
    except Exception as exc:
        logger.warning("Failed to load thesis validator verdicts: %s", exc)
        return {}
    return {
        option_symbol: _summarize_run(run)
        for option_symbol, run in latest_runs.items()
    }
