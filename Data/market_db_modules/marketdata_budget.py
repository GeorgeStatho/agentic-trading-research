"""Durable, conservative allowance shared by MarketData EOD callers."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from math import ceil, isfinite
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from db_common import get_connection


MARKET_TIMEZONE = ZoneInfo("America/New_York")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _window(now: datetime) -> tuple[str, str]:
    local = now.astimezone(MARKET_TIMEZONE)
    day = local.date()
    if local.time() < time(9, 30):
        day -= timedelta(days=1)
    start = datetime.combine(day, time(9, 30), MARKET_TIMEZONE)
    end = datetime.combine(day + timedelta(days=1), time(9, 30), MARKET_TIMEZONE)
    return start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


class MarketDataBudget:
    def __init__(self, db_path: Path | str):
        self.db_path = db_path
        raw = os.getenv("MARKETDATA_DAILY_CREDIT_BUDGET", "100")
        try:
            self.limit = int(raw)
        except ValueError:
            raise ValueError("MARKETDATA_DAILY_CREDIT_BUDGET must be a non-negative integer.") from None
        if self.limit < 0:
            raise ValueError("MARKETDATA_DAILY_CREDIT_BUDGET must be a non-negative integer.")

    def _state(self, conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
        start, end = _window(now)
        conn.execute(
            "INSERT OR IGNORE INTO marketdata_request_budget (id, window_start, window_end) VALUES (1, ?, ?)",
            (start, end),
        )
        conn.execute(
            """UPDATE marketdata_request_budget SET window_start = ?, window_end = ?, credits_used = 0
               WHERE id = 1 AND window_start != ?""", (start, end, start),
        )
        conn.execute(
            """UPDATE marketdata_request_budget SET blocked_until = NULL, block_reason = NULL
               WHERE id = 1 AND blocked_until <= ?""", (now.isoformat(),),
        )
        return dict(conn.execute("SELECT * FROM marketdata_request_budget WHERE id = 1").fetchone())

    def _summary(self, state: dict[str, Any]) -> dict[str, Any]:
        reason, resume = state["block_reason"], state["blocked_until"]
        if state["credits_used"] >= self.limit:
            reason = "daily_credit_budget_exhausted"
            resume = max(resume or "", state["window_end"])
        return {"daily_limit": self.limit, "used": state["credits_used"],
                "remaining": max(0, self.limit - state["credits_used"]),
                "resets_at": state["window_end"], "stop_reason": reason, "resume_at": resume}

    def snapshot(self) -> dict[str, Any]:
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._summary(self._state(conn, _now()))

    def reserve(self) -> tuple[str | None, dict[str, Any]]:
        """Commit before I/O, so concurrent callers cannot reuse the allowance."""
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            state = self._state(conn, _now())
            summary = self._summary(state)
            if summary["stop_reason"]:
                return None, summary
            conn.execute("UPDATE marketdata_request_budget SET credits_used = credits_used + 1 WHERE id = 1")
            return state["window_start"], summary

    def observe(self, reservation: str, response: Any) -> dict[str, Any]:
        """Keep at least one unit per attempt, including failures and free responses.

        This deliberately bounds retries even when the provider charges no credits.
        Missing response headers never refund the reservation.
        """
        now = _now()
        headers = {str(k).lower(): v for k, v in (getattr(response, "headers", {}) or {}).items()}
        consumed = _number(headers.get("x-api-ratelimit-consumed"))
        remaining = _number(headers.get("x-api-ratelimit-remaining"))
        reset = _number(headers.get("x-api-ratelimit-reset"))
        retry = headers.get("retry-after")
        retry_seconds = _number(retry)
        retry_at = None
        try:
            if retry_seconds is not None:
                retry_at = now + timedelta(seconds=retry_seconds)
            elif retry:
                retry_at = parsedate_to_datetime(retry).astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            retry_at = None
        try:
            reset_at = datetime.fromtimestamp(reset, timezone.utc) if reset is not None else None
        except (ValueError, OverflowError, OSError):
            reset_at = None
        limited = getattr(response, "status_code", None) == 429
        reason = "provider_credits_exhausted" if remaining == 0 else "provider_rate_limited" if limited else None
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            state = self._state(conn, now)
            if consumed is not None:
                # Usually one credit for this single-symbol endpoint. Honour higher reported costs.
                extra = max(1, ceil(consumed)) - (1 if reservation == state["window_start"] else 0)
                conn.execute("UPDATE marketdata_request_budget SET credits_used = credits_used + ? WHERE id = 1", (extra,))
            if reason:
                # With positive remaining credits, 429 can be a short concurrency limit.
                # Prefer Retry-After in that case instead of waiting an entire day.
                provider_reset = reset_at if remaining == 0 or retry_at is None else None
                candidates = [at for at in (retry_at, provider_reset) if at and at > now]
                until = max(candidates).isoformat() if candidates else state["window_end"]
                until = max(until, state["blocked_until"] or "")
                conn.execute("UPDATE marketdata_request_budget SET blocked_until = ?, block_reason = ? WHERE id = 1", (until, reason))
            return self._summary(dict(conn.execute("SELECT * FROM marketdata_request_budget WHERE id = 1").fetchone()))
