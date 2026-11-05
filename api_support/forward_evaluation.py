from __future__ import annotations

"""Read-side view of forward evaluation (research Phase 7) for the V2 Rollout page.

Cohorts and their pre-registered windows, paired incumbent/challenger coverage, the latest
comparison per cohort judged by its frozen acceptance criteria, closing-mark sessions, the
discovery route ledger and research-route outcomes. These are shadow research records: nothing
here places an order. Like the V2 status reading, nothing here initializes or migrates the
database, so a missing table is reported as missing.
"""

from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from db_common import get_connection

DEFAULT_WINDOW_DAYS = 30
REQUIRED_TABLES = ("research_run_acceptance", "decision_observations", "forward_comparison_reports",
                   "research_thesis_outcomes", "research_run_pauses", "discovery_production_routes",
                   "company_opportunist_impacts")


def build_forward_evaluation_payload(*, window_days: int = DEFAULT_WINDOW_DAYS, db_path: Any = None) -> dict:
    from market_db_modules.discovery_evaluation import route_ledger
    from market_db_modules.forward_comparison import latest_comparisons
    from market_db_modules.forward_evaluation import forward_status_from
    from market_db_modules.research_outcomes import research_route_summary

    as_of = datetime.now(timezone.utc).isoformat()
    settings = _settings()
    with closing(get_connection(db_path)) as conn:
        missing = [name for name in REQUIRED_TABLES if not _table_exists(conn, name)]
        if missing:
            return {"as_of": as_of, "available": False, "settings": settings,
                    "reason": "Forward-evaluation tables do not exist in this database yet; the next normal "
                              "start of any service creates them."}
        discovery = route_ledger(conn, days=window_days)
        discovery.pop("admitted_events", None)
        return {"as_of": as_of, "available": True, "window_days": window_days, "settings": settings,
                **forward_status_from(conn), "comparisons": latest_comparisons(conn),
                "discovery": discovery, "research_route": research_route_summary(conn, days=window_days)}


def _settings() -> dict:
    from services.evaluation.forward_evaluation_policy import AcceptancePolicy, ChallengerPolicy, ForwardEvaluationPolicy

    result: dict[str, Any] = {}
    for key, read in (("forward_evaluation", lambda: {"mode": ForwardEvaluationPolicy.from_env().mode}),
                      ("challenger", lambda: ChallengerPolicy.from_env().manifest()),
                      ("acceptance_for_new_cohorts", lambda: AcceptancePolicy.from_env().assumptions())):
        try:
            result[key] = read()
        except ValueError as exc:
            result[key] = {"configuration_error": str(exc)}
    return result


def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
