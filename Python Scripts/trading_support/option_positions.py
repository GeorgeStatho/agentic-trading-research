from __future__ import annotations

from pathlib import Path
from typing import Any

from alpaca.trading.client import TradingClient

from .clients import (
    get_default_trading_client,
    get_option_history_client,
    get_stock_history_client,
)
from ._option_positions_momentum import MOMENTUM_MIXED
from ._option_positions_defaults import *
from ._option_positions_manager import (
    CloseOptionPositions as _close_option_positions_impl,
    ManageCurrentOptionPositions as _manage_current_option_positions_impl,
)
from ._option_positions_market import (
    _days_to_expiration,
    _hours_to_expiration,
    _is_option_position_symbol,
    _normalize_pct_ratio,
    _normalize_unrealized_pl_pct,
    _parse_option_symbol,
    _resolve_dte_exit_rule,
    _resolve_option_exit_thresholds,
    _resolve_trailing_giveback_pct,
    _get_latest_option_quote as _get_latest_option_quote_impl,
    _get_latest_stock_price as _get_latest_stock_price_impl,
)
from ._option_positions_snapshot import _build_option_position_snapshot as _build_option_position_snapshot_impl
from ._option_positions_state import *
from ._option_positions_state import _reconcile_pending_order_state
from ._option_positions_strategy import *
from ._option_positions_strategy import _structured_exit_action
from services.option_momentum import (
    MOMENTUM_BAD,
    MOMENTUM_GOOD,
    MOMENTUM_UNKNOWN,
    evaluate_option_momentum_from_snapshot,
)
from services.option_position_state import delete_position_state, get_position_state, update_position_state


def _get_latest_option_quote(symbol: str) -> dict[str, Any]:
    return _get_latest_option_quote_impl(symbol, get_option_history_client)


def _get_latest_stock_price(symbol: str) -> dict[str, Any]:
    return _get_latest_stock_price_impl(symbol, get_stock_history_client)


def _build_option_position_snapshot(
    position: Any,
    *,
    take_profit_pct: float,
    stop_loss_pct: float,
    exit_hours_to_expiration: float,
    enable_trailing_profit: bool,
    trailing_profit_dry_run: bool,
    trailing_profit_config: TrailingProfitConfig,
    momentum_history_config: MomentumHistoryConfig,
    state_path: Path,
    position_state_override: dict[str, Any] | None = None,
    pending_reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _build_option_position_snapshot_impl(
        position,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        exit_hours_to_expiration=exit_hours_to_expiration,
        enable_trailing_profit=enable_trailing_profit,
        trailing_profit_dry_run=trailing_profit_dry_run,
        trailing_profit_config=trailing_profit_config,
        momentum_history_config=momentum_history_config,
        state_path=state_path,
        get_latest_option_quote=_get_latest_option_quote,
        get_latest_stock_price=_get_latest_stock_price,
        position_state_override=position_state_override,
        pending_reconciliation=pending_reconciliation,
    )


def CloseOptionPositions(
    company: str | None = None,
    *,
    trading_client_override: TradingClient | None = None,
) -> list[dict[str, Any]]:
    return _close_option_positions_impl(
        company,
        trading_client_override=trading_client_override,
        get_default_trading_client=get_default_trading_client,
    )


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
) -> dict[str, Any]:
    return _manage_current_option_positions_impl(
        company,
        execute_sales=execute_sales,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        exit_hours_to_expiration=exit_hours_to_expiration,
        state_path_override=state_path_override,
        enable_trailing_profit_override=enable_trailing_profit_override,
        trailing_profit_dry_run_override=trailing_profit_dry_run_override,
        enable_momentum_exit_override=enable_momentum_exit_override,
        momentum_history_enable_after_pnl_pct_override=momentum_history_enable_after_pnl_pct_override,
        momentum_history_window_size_override=momentum_history_window_size_override,
        momentum_history_min_samples_override=momentum_history_min_samples_override,
        momentum_history_bad_count_exit_threshold_override=momentum_history_bad_count_exit_threshold_override,
        momentum_history_consecutive_bad_exit_threshold_override=momentum_history_consecutive_bad_exit_threshold_override,
        trail_protection_trigger_pct_override=trail_protection_trigger_pct_override,
        trail_initial_floor_pct_override=trail_initial_floor_pct_override,
        trail_first_scale_out_trigger_pct_override=trail_first_scale_out_trigger_pct_override,
        trail_first_scale_out_fraction_override=trail_first_scale_out_fraction_override,
        trail_second_scale_out_trigger_pct_override=trail_second_scale_out_trigger_pct_override,
        trail_second_scale_out_fraction_override=trail_second_scale_out_fraction_override,
        trail_giveback_pct_by_bucket_key_override=trail_giveback_pct_by_bucket_key_override,
        trail_3_7_giveback_pct_override=trail_3_7_giveback_pct_override,
        trail_7_14_giveback_pct_override=trail_7_14_giveback_pct_override,
        trail_14_30_giveback_pct_override=trail_14_30_giveback_pct_override,
        trail_30_45_giveback_pct_override=trail_30_45_giveback_pct_override,
        trail_45_60_giveback_pct_override=trail_45_60_giveback_pct_override,
        trail_100_floor_pct_override=trail_100_floor_pct_override,
        trail_150_floor_pct_override=trail_150_floor_pct_override,
        trail_200_floor_pct_override=trail_200_floor_pct_override,
        pending_exit_stale_minutes_override=pending_exit_stale_minutes_override,
        pending_exit_cancel_on_stale_override=pending_exit_cancel_on_stale_override,
        submit_full_exit_order=submit_full_exit_order,
        submit_partial_exit_order=submit_partial_exit_order,
        resolve_order_status=resolve_order_status,
        cancel_order=cancel_order,
        trading_client_override=trading_client_override,
        get_default_trading_client=get_default_trading_client,
        build_option_position_snapshot=_build_option_position_snapshot,
    )
