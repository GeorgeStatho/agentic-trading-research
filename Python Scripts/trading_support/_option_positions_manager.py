from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from alpaca.trading.client import TradingClient

from services.option_position_state import delete_position_state, get_position_state, load_state

from ._option_positions_defaults import (
    DEFAULT_OPTION_EXIT_HOURS_TO_EXPIRATION,
    DEFAULT_OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD,
    DEFAULT_OPTION_MOMENTUM_HISTORY_CONSECUTIVE_BAD_EXIT_THRESHOLD,
    DEFAULT_OPTION_MOMENTUM_HISTORY_ENABLE_AFTER_PNL_PCT,
    DEFAULT_OPTION_MOMENTUM_HISTORY_MIN_SAMPLES,
    DEFAULT_OPTION_MOMENTUM_HISTORY_WINDOW_SIZE,
    DEFAULT_OPTION_PRICE_MOMENTUM_BAD_GIVEBACK_THRESHOLD,
    DEFAULT_OPTION_PRICE_MOMENTUM_GOOD_PROFIT_THRESHOLD,
    DEFAULT_OPTION_PRICE_MOMENTUM_MIXED_GIVEBACK_THRESHOLD,
    DEFAULT_OPTION_PENDING_EXIT_CANCEL_ON_STALE,
    DEFAULT_OPTION_PENDING_EXIT_STALE_MINUTES,
    DEFAULT_OPTION_POSITION_ENABLE_MOMENTUM_EXIT,
    DEFAULT_OPTION_POSITION_ENABLE_TRAILING_PROFIT,
    DEFAULT_OPTION_POSITION_TRAILING_PROFIT_DRY_RUN,
    DEFAULT_OPTION_STOP_LOSS_PCT,
    DEFAULT_OPTION_TAKE_PROFIT_PCT,
    DEFAULT_OPTION_TRAIL_100_FLOOR_PCT,
    DEFAULT_OPTION_TRAIL_150_FLOOR_PCT,
    DEFAULT_OPTION_TRAIL_200_FLOOR_PCT,
    DEFAULT_OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION,
    DEFAULT_OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT,
    DEFAULT_OPTION_TRAIL_PROTECTION_TRIGGER_PCT,
    DEFAULT_OPTION_TRAIL_INITIAL_FLOOR_PCT,
    DEFAULT_OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION,
    DEFAULT_OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT,
    DEFAULT_OPTION_TRAILING_GIVEBACK_PCT_BY_BUCKET_KEY,
    MomentumHistoryConfig,
    OptionPriceMomentumConfig,
    PendingExitConfig,
    TrailingProfitConfig,
)
from ._option_positions_market import _is_option_position_symbol
from ._option_positions_state import (
    _coerce_bool,
    _mark_exit_submission,
    _reconcile_pending_order_state,
    _resolve_option_position_state_path,
    _safe_int,
)


def CloseOptionPositions(
    company: str | None = None,
    *,
    trading_client_override: TradingClient | None = None,
    get_default_trading_client: Callable[[], TradingClient],
) -> list[dict[str, Any]]:
    close_results: list[dict[str, Any]] = []
    active_trading_client = trading_client_override or get_default_trading_client()
    positions = active_trading_client.get_all_positions()
    for position in positions or []:
        symbol = str(getattr(position, "symbol", "") or "").strip().upper()
        if not _is_option_position_symbol(symbol, company):
            continue
        close_order = active_trading_client.close_position(symbol)
        close_results.append(
            {
                "symbol": symbol,
                "qty": str(getattr(position, "qty", "")),
                "close_order_id": str(getattr(close_order, "id", "")),
                "close_order_status": str(getattr(close_order, "status", "")),
            }
        )
    return close_results


def ManageCurrentOptionPositions(
    company: str | None = None,
    *,
    execute_sales: bool = False,
    take_profit_pct: float = DEFAULT_OPTION_TAKE_PROFIT_PCT,
    stop_loss_pct: float = DEFAULT_OPTION_STOP_LOSS_PCT,
    exit_hours_to_expiration: float = DEFAULT_OPTION_EXIT_HOURS_TO_EXPIRATION,
    state_path_override: Path | None = None,
    enable_trailing_profit_override: bool | None = None,
    trailing_profit_dry_run_override: bool | None = None,
    enable_momentum_exit_override: bool | None = None,
    momentum_history_enable_after_pnl_pct_override: float | None = None,
    momentum_history_window_size_override: int | None = None,
    momentum_history_min_samples_override: int | None = None,
    momentum_history_bad_count_exit_threshold_override: int | None = None,
    momentum_history_consecutive_bad_exit_threshold_override: int | None = None,
    option_price_momentum_bad_giveback_threshold_override: float | None = None,
    option_price_momentum_mixed_giveback_threshold_override: float | None = None,
    option_price_momentum_good_profit_threshold_override: float | None = None,
    trail_protection_trigger_pct_override: float | None = None,
    trail_initial_floor_pct_override: float | None = None,
    trail_first_scale_out_trigger_pct_override: float | None = None,
    trail_first_scale_out_fraction_override: float | None = None,
    trail_second_scale_out_trigger_pct_override: float | None = None,
    trail_second_scale_out_fraction_override: float | None = None,
    trail_giveback_pct_by_bucket_key_override: dict[str, float] | None = None,
    trail_3_7_giveback_pct_override: float | None = None,
    trail_7_14_giveback_pct_override: float | None = None,
    trail_14_30_giveback_pct_override: float | None = None,
    trail_30_45_giveback_pct_override: float | None = None,
    trail_45_60_giveback_pct_override: float | None = None,
    trail_100_floor_pct_override: float | None = None,
    trail_150_floor_pct_override: float | None = None,
    trail_200_floor_pct_override: float | None = None,
    pending_exit_stale_minutes_override: float | None = None,
    pending_exit_cancel_on_stale_override: bool | None = None,
    submit_full_exit_order: Any | None = None,
    submit_partial_exit_order: Any | None = None,
    resolve_order_status: Any | None = None,
    cancel_order: Any | None = None,
    trading_client_override: TradingClient | None = None,
    get_default_trading_client: Callable[[], TradingClient] | None = None,
    build_option_position_snapshot: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if get_default_trading_client is None or build_option_position_snapshot is None:
        raise ValueError("Manager implementation requires client and snapshot callbacks.")
    position_summaries: list[dict[str, Any]] = []
    active_trading_client = trading_client_override or get_default_trading_client()
    positions = active_trading_client.get_all_positions()
    state_path = state_path_override or _resolve_option_position_state_path()
    enable_trailing_profit = (
        DEFAULT_OPTION_POSITION_ENABLE_TRAILING_PROFIT
        if enable_trailing_profit_override is None
        else bool(enable_trailing_profit_override)
    )
    if enable_trailing_profit_override is None:
        enable_trailing_profit = _coerce_bool(
            os.getenv("OPTION_POSITION_ENABLE_TRAILING_PROFIT"),
            DEFAULT_OPTION_POSITION_ENABLE_TRAILING_PROFIT,
        )
    trailing_profit_dry_run = (
        DEFAULT_OPTION_POSITION_TRAILING_PROFIT_DRY_RUN
        if trailing_profit_dry_run_override is None
        else bool(trailing_profit_dry_run_override)
    )
    if trailing_profit_dry_run_override is None:
        trailing_profit_dry_run = _coerce_bool(
            os.getenv("OPTION_POSITION_TRAILING_PROFIT_DRY_RUN"),
            DEFAULT_OPTION_POSITION_TRAILING_PROFIT_DRY_RUN,
        )
    giveback_pct_by_bucket_key = dict(DEFAULT_OPTION_TRAILING_GIVEBACK_PCT_BY_BUCKET_KEY)
    if trail_giveback_pct_by_bucket_key_override:
        for bucket_key, value in trail_giveback_pct_by_bucket_key_override.items():
            if bucket_key not in DEFAULT_OPTION_TRAILING_GIVEBACK_PCT_BY_BUCKET_KEY or value in (None, ""):
                continue
            try:
                giveback_pct_by_bucket_key[bucket_key] = float(value)
            except (TypeError, ValueError):
                continue
    if trail_3_7_giveback_pct_override is not None and "3_7" in giveback_pct_by_bucket_key:
        giveback_pct_by_bucket_key["3_7"] = float(trail_3_7_giveback_pct_override)
    if trail_7_14_giveback_pct_override is not None:
        giveback_pct_by_bucket_key["7_14"] = float(trail_7_14_giveback_pct_override)
    if trail_14_30_giveback_pct_override is not None:
        giveback_pct_by_bucket_key["14_30"] = float(trail_14_30_giveback_pct_override)
    if trail_30_45_giveback_pct_override is not None:
        giveback_pct_by_bucket_key["30_45"] = float(trail_30_45_giveback_pct_override)
    if trail_45_60_giveback_pct_override is not None:
        giveback_pct_by_bucket_key["45_60"] = float(trail_45_60_giveback_pct_override)
    trailing_profit_config = TrailingProfitConfig(
        protection_trigger_pct=float(DEFAULT_OPTION_TRAIL_PROTECTION_TRIGGER_PCT if trail_protection_trigger_pct_override is None else trail_protection_trigger_pct_override),
        initial_floor_pct=float(DEFAULT_OPTION_TRAIL_INITIAL_FLOOR_PCT if trail_initial_floor_pct_override is None else trail_initial_floor_pct_override),
        first_scale_out_trigger_pct=float(DEFAULT_OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT if trail_first_scale_out_trigger_pct_override is None else trail_first_scale_out_trigger_pct_override),
        first_scale_out_fraction=float(DEFAULT_OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION if trail_first_scale_out_fraction_override is None else trail_first_scale_out_fraction_override),
        second_scale_out_trigger_pct=float(DEFAULT_OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT if trail_second_scale_out_trigger_pct_override is None else trail_second_scale_out_trigger_pct_override),
        second_scale_out_fraction=float(DEFAULT_OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION if trail_second_scale_out_fraction_override is None else trail_second_scale_out_fraction_override),
        giveback_pct_by_bucket_key=giveback_pct_by_bucket_key,
        floor_100_pct=float(DEFAULT_OPTION_TRAIL_100_FLOOR_PCT if trail_100_floor_pct_override is None else trail_100_floor_pct_override),
        floor_150_pct=float(DEFAULT_OPTION_TRAIL_150_FLOOR_PCT if trail_150_floor_pct_override is None else trail_150_floor_pct_override),
        floor_200_pct=float(DEFAULT_OPTION_TRAIL_200_FLOOR_PCT if trail_200_floor_pct_override is None else trail_200_floor_pct_override),
        enable_momentum_exit=(
            DEFAULT_OPTION_POSITION_ENABLE_MOMENTUM_EXIT
            if enable_momentum_exit_override is None
            else bool(enable_momentum_exit_override)
        ),
    )
    momentum_history_config = MomentumHistoryConfig(
        enable_after_pnl_pct=float(DEFAULT_OPTION_MOMENTUM_HISTORY_ENABLE_AFTER_PNL_PCT if momentum_history_enable_after_pnl_pct_override is None else momentum_history_enable_after_pnl_pct_override),
        window_size=max(1, int(DEFAULT_OPTION_MOMENTUM_HISTORY_WINDOW_SIZE if momentum_history_window_size_override is None else momentum_history_window_size_override)),
        min_samples=max(1, int(DEFAULT_OPTION_MOMENTUM_HISTORY_MIN_SAMPLES if momentum_history_min_samples_override is None else momentum_history_min_samples_override)),
        bad_count_exit_threshold=max(1, int(DEFAULT_OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD if momentum_history_bad_count_exit_threshold_override is None else momentum_history_bad_count_exit_threshold_override)),
        consecutive_bad_exit_threshold=max(1, int(DEFAULT_OPTION_MOMENTUM_HISTORY_CONSECUTIVE_BAD_EXIT_THRESHOLD if momentum_history_consecutive_bad_exit_threshold_override is None else momentum_history_consecutive_bad_exit_threshold_override)),
    )
    option_price_momentum_config = OptionPriceMomentumConfig(
        bad_giveback_threshold=float(
            DEFAULT_OPTION_PRICE_MOMENTUM_BAD_GIVEBACK_THRESHOLD
            if option_price_momentum_bad_giveback_threshold_override is None
            else option_price_momentum_bad_giveback_threshold_override
        ),
        mixed_giveback_threshold=float(
            DEFAULT_OPTION_PRICE_MOMENTUM_MIXED_GIVEBACK_THRESHOLD
            if option_price_momentum_mixed_giveback_threshold_override is None
            else option_price_momentum_mixed_giveback_threshold_override
        ),
        good_profit_threshold=float(
            DEFAULT_OPTION_PRICE_MOMENTUM_GOOD_PROFIT_THRESHOLD
            if option_price_momentum_good_profit_threshold_override is None
            else option_price_momentum_good_profit_threshold_override
        ),
    )
    pending_exit_config = PendingExitConfig(
        stale_minutes=float(DEFAULT_OPTION_PENDING_EXIT_STALE_MINUTES if pending_exit_stale_minutes_override is None else pending_exit_stale_minutes_override),
        cancel_on_stale=(DEFAULT_OPTION_PENDING_EXIT_CANCEL_ON_STALE if pending_exit_cancel_on_stale_override is None else bool(pending_exit_cancel_on_stale_override)),
    )
    open_option_symbols: set[str] = set()
    for position in positions or []:
        symbol = str(getattr(position, "symbol", "") or "").strip().upper()
        if not _is_option_position_symbol(symbol, company):
            continue
        open_option_symbols.add(symbol)
        broker_quantity = _safe_int(getattr(position, "qty", None)) or 0
        position_state = get_position_state(state_path, symbol)
        reconciled_state, pending_reconciliation = _reconcile_pending_order_state(
            state_path=state_path,
            option_symbol=symbol,
            position_state=position_state,
            resolve_order_status=resolve_order_status,
            cancel_order=cancel_order,
            broker_quantity=broker_quantity,
            pending_exit_config=pending_exit_config,
        )
        summary = build_option_position_snapshot(
            position,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            exit_hours_to_expiration=exit_hours_to_expiration,
            enable_trailing_profit=enable_trailing_profit,
            trailing_profit_dry_run=trailing_profit_dry_run,
            trailing_profit_config=trailing_profit_config,
            momentum_history_config=momentum_history_config,
            option_price_momentum_config=option_price_momentum_config,
            state_path=state_path,
            position_state_override=reconciled_state,
            pending_reconciliation=pending_reconciliation,
        )
        if execute_sales and summary["exit_action"] == "sell_full":
            if trailing_profit_dry_run:
                summary["close_error"] = "Dry-run enabled; no sell order was submitted."
            else:
                try:
                    if submit_full_exit_order is not None:
                        close_order = submit_full_exit_order(symbol)
                    else:
                        legacy_close_order = active_trading_client.close_position(symbol)
                        close_order = {
                            "id": str(getattr(legacy_close_order, "id", "")),
                            "status": str(getattr(legacy_close_order, "status", "")),
                            "side": str(getattr(legacy_close_order, "side", "sell")),
                            "qty": str(getattr(legacy_close_order, "qty", summary.get("quantity") or "")),
                            "submitted_at": str(getattr(legacy_close_order, "submitted_at", "")),
                        }
                    summary["close_submitted"] = True
                    summary["close_order_id"] = str(close_order.get("id") or "")
                    summary["close_order_status"] = str(close_order.get("status") or "")
                    summary["close_order_side"] = str(close_order.get("side") or "")
                    summary["close_requested_qty"] = (
                        _safe_int(close_order.get("requested_qty"))
                        or _safe_int(close_order.get("qty"))
                        or (_safe_int(summary.get("quantity")) or 0)
                    )
                    _mark_exit_submission(
                        state_path=state_path,
                        option_symbol=symbol,
                        summary=summary,
                        order_summary=close_order,
                    )
                except Exception as exc:
                    summary["close_error"] = str(exc)
        elif execute_sales and summary["exit_action"] == "sell_partial":
            if trailing_profit_dry_run:
                summary["close_error"] = "Dry-run enabled; partial sell was not submitted."
            elif submit_partial_exit_order is None:
                summary["close_error"] = "Partial sell action computed by structured engine, but no partial option sell callback was provided."
            else:
                try:
                    close_order = submit_partial_exit_order(symbol, int(summary.get("qty_to_sell") or 0))
                    summary["close_submitted"] = True
                    summary["close_order_id"] = str(close_order.get("id") or "")
                    summary["close_order_status"] = str(close_order.get("status") or "")
                    summary["close_order_side"] = str(close_order.get("side") or "")
                    summary["close_requested_qty"] = (
                        _safe_int(close_order.get("requested_qty"))
                        or _safe_int(close_order.get("qty"))
                        or (_safe_int(summary.get("qty_to_sell")) or 0)
                    )
                    _mark_exit_submission(
                        state_path=state_path,
                        option_symbol=symbol,
                        summary=summary,
                        order_summary=close_order,
                    )
                except Exception as exc:
                    summary["close_error"] = str(exc)
        position_summaries.append(summary)
    if not company:
        try:
            stale_symbols = set(load_state(state_path).get("positions", {}).keys()).difference(open_option_symbols)
        except Exception:
            stale_symbols = set()
        for stale_symbol in stale_symbols:
            delete_position_state(state_path, stale_symbol)
    return {
        "evaluated_at": datetime.now().isoformat(),
        "company_filter": str(company or "").strip().upper(),
        "execute_sales": execute_sales,
        "trailing_profit_enabled": enable_trailing_profit,
        "trailing_profit_dry_run": trailing_profit_dry_run,
        "option_position_state_path": str(state_path),
        "uses_dte_based_exit_thresholds": True,
        "take_profit_pct": take_profit_pct,
        "stop_loss_pct": stop_loss_pct,
        "exit_hours_to_expiration": exit_hours_to_expiration,
        "position_count": len(position_summaries),
        "sell_count": sum(1 for summary in position_summaries if summary.get("exit_action") in {"sell_full", "sell_partial"}),
        "full_sell_count": sum(1 for summary in position_summaries if summary.get("exit_action") == "sell_full"),
        "partial_sell_count": sum(1 for summary in position_summaries if summary.get("exit_action") == "sell_partial"),
        "close_submitted_count": sum(1 for summary in position_summaries if summary.get("close_submitted")),
        "positions": position_summaries,
    }
