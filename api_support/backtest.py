from __future__ import annotations

from typing import Any

from market_db_modules.backtest_alpaca_bars import fetch_alpaca_minute_bars
from market_db_modules.backtest_marketdata import fetch_marketdata_eod_option_quotes
from market_db_modules.backtesting import get_backtest_run, list_backtest_runs, run_backtest


def build_backtest_runs_payload(*, limit: int = 25) -> dict[str, Any]:
    return {
        "runs": list_backtest_runs(limit=limit),
        "read_only": True,
        "no_trade_executed_by_feature": True,
    }


def build_backtest_run_payload(run_id: int) -> dict[str, Any]:
    return get_backtest_run(run_id)


def create_backtest_run(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("config")
    if config is not None and not isinstance(config, dict):
        raise ValueError("config must be an object.")
    raw_limit = payload.get("limit", 250)
    limit = None
    if raw_limit is not None:
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be an integer or null for all decisions.") from exc
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000, or null for all decisions.")
    return run_backtest(
        config=config,
        start_at=str(payload.get("start_at") or "") or None,
        end_at=str(payload.get("end_at") or "") or None,
        limit=limit,
    )


def fetch_backtest_eod_quotes(payload: dict[str, Any]) -> dict[str, Any]:
    """Populate historical EOD quotes without exposing provider credentials to clients."""
    try:
        limit = int(payload.get("limit", 10))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer.") from exc
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100.")

    try:
        entry_delay_minutes = int(payload.get("entry_delay_minutes", 1440))
        max_hold_hours = int(payload.get("max_hold_hours", 168))
        timeout_seconds = float(payload.get("timeout_seconds", 30))
    except (TypeError, ValueError) as exc:
        raise ValueError("EOD fetch timing settings must be numeric.") from exc
    if entry_delay_minutes < 1 or max_hold_hours < 1:
        raise ValueError("entry_delay_minutes and max_hold_hours must be at least 1.")
    if timeout_seconds <= 0 or timeout_seconds > 120:
        raise ValueError("timeout_seconds must be greater than 0 and no more than 120.")

    force = payload.get("force", False)
    if not isinstance(force, bool):
        raise ValueError("force must be a boolean.")

    start_at = str(payload.get("start_at") or "") or None
    end_at = str(payload.get("end_at") or "") or None
    result = fetch_marketdata_eod_option_quotes(
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        entry_delay_minutes=entry_delay_minutes,
        max_hold_hours=max_hold_hours,
        timeout_seconds=timeout_seconds,
        force=force,
    )
    return {
        **result,
        "read_only": True,
        "no_trade_executed_by_feature": True,
    }


def fetch_backtest_minute_bars(payload: dict[str, Any]) -> dict[str, Any]:
    """Store Alpaca 1-minute stock and option bars for stored decisions' hold windows; reads market data only."""
    try:
        limit = int(payload.get("limit", 10))
        max_hold_hours = int(payload.get("max_hold_hours", 168))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit and max_hold_hours must be integers.") from exc
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100.")
    if max_hold_hours < 1:
        raise ValueError("max_hold_hours must be at least 1.")
    result = fetch_alpaca_minute_bars(start_at=str(payload.get("start_at") or "") or None,
                                      end_at=str(payload.get("end_at") or "") or None,
                                      limit=limit, max_hold_hours=max_hold_hours)
    return {**result, "read_only": True, "no_trade_executed_by_feature": True}
