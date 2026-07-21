from __future__ import annotations

from typing import Any

from db_helpers.market import get_backtest_run, list_backtest_runs, run_backtest


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
    try:
        limit = int(payload.get("limit", 250))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer.") from exc
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000.")
    return run_backtest(
        config=config,
        start_at=str(payload.get("start_at") or "") or None,
        end_at=str(payload.get("end_at") or "") or None,
        limit=limit,
    )
