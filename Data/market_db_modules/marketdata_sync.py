"""One durable, budgeted EOD cache refresh per New York calendar day."""
from __future__ import annotations

import json
import os
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from db_common import get_connection
from .backtest_marketdata import fetch_marketdata_eod_option_quotes


MARKET_TIMEZONE = ZoneInfo("America/New_York")


def _positive_setting(name: str, default: int, maximum: int | None = None) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1 or (maximum is not None and value > maximum):
        raise ValueError(f"{name} must be positive" + (f" and no greater than {maximum}." if maximum else "."))
    return value


@dataclass(frozen=True)
class EodSyncSettings:
    enabled: bool = True
    batch_size: int = 100
    entry_delay_minutes: int = 1440
    max_hold_hours: int = 168
    publication_delay_hours: int = 24
    retry_days: int = 7

    @classmethod
    def from_env(cls) -> EodSyncSettings:
        return cls(
            enabled=os.getenv("MARKETDATA_AUTO_FETCH_ENABLED", "true").strip().lower() not in {"false", "0", "no", "off", ""},
            batch_size=_positive_setting("MARKETDATA_AUTO_FETCH_BATCH_SIZE", 100, 100),
            entry_delay_minutes=_positive_setting("MARKETDATA_AUTO_FETCH_ENTRY_DELAY_MINUTES", 1440),
            max_hold_hours=_positive_setting("MARKETDATA_AUTO_FETCH_MAX_HOLD_HOURS", 168),
            publication_delay_hours=_positive_setting("MARKETDATA_AUTO_FETCH_PUBLICATION_DELAY_HOURS", 24),
            retry_days=_positive_setting("MARKETDATA_AUTO_FETCH_RETRY_DAYS", 7),
        )


def run_due_eod_sync(
    settings: EodSyncSettings,
    *,
    db_path: Path | str | None = None,
    now: datetime | None = None,
    fetcher: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run after 10:00 ET, at most once daily, against an initialized database.

    A committed claim prevents duplicate batches after restarts or concurrent
    scheduler processes. If interrupted, the next day's run resumes the backlog;
    the fetcher commits individual windows and enforces its shared daily budget.
    """
    if not settings.enabled:
        return {"status": "disabled"}
    if not str(os.getenv("MARKETDATA_TOKEN") or "").strip():
        return {"status": "missing_token"}
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    local = current.astimezone(MARKET_TIMEZONE)
    if local.time() < time(10):
        return {"status": "not_due"}
    schedule_date = local.date().isoformat()
    with closing(get_connection(db_path)) as conn, conn:
        claimed = conn.execute(
            """INSERT OR IGNORE INTO marketdata_eod_sync_runs (schedule_date, started_at, status)
               VALUES (?, ?, 'running')""", (schedule_date, current.isoformat()),
        ).rowcount
    if not claimed:
        return {"status": "already_ran", "schedule_date": schedule_date}

    # Whole holding window plus publication buffer; avoid refetching still-open windows.
    cutoff = current - timedelta(minutes=settings.entry_delay_minutes,
                                 hours=settings.max_hold_hours + settings.publication_delay_hours)
    try:
        result = (fetcher or fetch_marketdata_eod_option_quotes)(
            db_path=db_path, end_at=cutoff.isoformat(), limit=settings.batch_size,
            entry_delay_minutes=settings.entry_delay_minutes, max_hold_hours=settings.max_hold_hours,
            retry_before_at=(current - timedelta(days=settings.retry_days)).isoformat(),
        )
        with closing(get_connection(db_path)) as conn, conn:
            conn.execute(
                """UPDATE marketdata_eod_sync_runs SET completed_at = ?, status = ?, fetch_run_id = ?,
                   summary_json = ? WHERE schedule_date = ?""",
                (datetime.now(timezone.utc).isoformat(), result["status"], result["fetch_run_id"],
                 json.dumps(result), schedule_date),
            )
        return {**result, "schedule_date": schedule_date}
    except Exception as exc:
        with closing(get_connection(db_path)) as conn, conn:
            conn.execute(
                """UPDATE marketdata_eod_sync_runs SET completed_at = ?, status = 'failed', error_text = ?
                   WHERE schedule_date = ?""",
                (datetime.now(timezone.utc).isoformat(), f"{type(exc).__name__}: {exc}", schedule_date),
            )
        raise
