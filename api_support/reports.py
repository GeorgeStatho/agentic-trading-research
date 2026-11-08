"""Payloads for the read-only report routes, shaped for the dashboard's tables."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone

from db_common import get_connection
from market_db_modules.decision_attribution_report import decision_attribution_report

ATTRIBUTION_HORIZONS = (5, 10)


def _groups(by_key: dict) -> list[dict]:
    # Keyed by data values ("weak", "high"); as rows they keep a fixed shape, largest first.
    rows = [{"key": key, "count": group["count"], "graded": group["graded"],
             "hit_rate": group["hit_rate"], "hit_rate_se": group["hit_rate_se"]} for key, group in by_key.items()]
    return sorted(rows, key=lambda row: -row["count"])


def decision_attribution_payload(*, days: int, horizon: int) -> dict:
    report = decision_attribution_report(days=days, horizon=horizon)
    failures = sorted(report["primary_failure_counts"].items(), key=lambda item: -item[1])
    return {
        "days": days, "horizon": horizon, "total": report["total"],
        "by_quality": _groups(report["matrix"]),
        "by_source": _groups(report["by_evidence_source"]),
        "by_self_rating": _groups(report["model_self_rating_calibration"]),
        "primary_failures": [{"key": key, "count": count} for key, count in failures],
    }


MACRO_EVENT_COLUMNS = ("event_name", "scheduled_at_utc", "country", "currency", "importance", "forecast", "previous")


def macro_calendar_payload(*, days: int, now: datetime | None = None) -> dict:
    """Scheduled macro events from now to `days` ahead, and how far the stored calendar runs.

    The calendar is fetched a week at a time, so late in a week the window can outrun it.
    """
    now = now or datetime.now(timezone.utc)
    with closing(get_connection()) as conn:
        events = conn.execute(
            f"""SELECT {', '.join(MACRO_EVENT_COLUMNS)} FROM macro_events
               WHERE scheduled_at_utc >= ? AND scheduled_at_utc < ? ORDER BY scheduled_at_utc""",
            (now.isoformat(), (now + timedelta(days=days)).isoformat()),
        ).fetchall()
        through = conn.execute("SELECT MAX(scheduled_at_utc) FROM macro_events").fetchone()[0]
    return {"days": days, "calendar_through": through, "events": [dict(event) for event in events]}


USAGE_SUMS = ("COUNT(*) AS calls, ROUND(COALESCE(SUM(cost_usd), 0), 4) AS cost_usd, "
              "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
              "COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens")
# Key expression and order for each breakdown; calls recorded before stages were show as "unknown".
USAGE_BREAKDOWNS = {
    "by_stage": ("COALESCE(stage, 'unknown')", "cost_usd DESC, calls DESC, key"),
    "by_model": ("model", "cost_usd DESC, calls DESC, key"),
    "by_provider": ("COALESCE(upstream_provider, 'unknown')", "cost_usd DESC, calls DESC, key"),
    "by_day": ("substr(called_at, 1, 10)", "key DESC"),
}


def llm_usage_payload(*, days: int, now: datetime | None = None) -> dict:
    """Hosted-model spend and tokens over the last `days`, in total and per stage, model, provider and day."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    with closing(get_connection()) as conn:
        totals = dict(conn.execute(f"SELECT {USAGE_SUMS} FROM llm_call_usage WHERE called_at >= ?", (since,)).fetchone())
        week = conn.execute("SELECT ROUND(COALESCE(SUM(cost_usd), 0), 4) FROM llm_call_usage WHERE called_at >= ?",
                            ((now - timedelta(days=7)).isoformat(),)).fetchone()[0]
        breakdowns = {name: [dict(row) for row in conn.execute(
            f"SELECT {key} AS key, {USAGE_SUMS} FROM llm_call_usage WHERE called_at >= ? GROUP BY 1 ORDER BY {order}",
            (since,))] for name, (key, order) in USAGE_BREAKDOWNS.items()}
    return {"days": days, "totals": totals, "last_7_days_cost_usd": week, **breakdowns}
