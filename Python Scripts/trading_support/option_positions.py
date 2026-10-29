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
from ._option_positions_momentum import evaluate_option_price_momentum
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
    _resolve_volatility_scale,
    _compute_atr_pct,
    _get_latest_option_quote as _get_latest_option_quote_impl,
    _get_latest_stock_price as _get_latest_stock_price_impl,
    _get_underlying_atr_pct as _get_underlying_atr_pct_impl,
)
from ._option_positions_snapshot import _build_option_position_snapshot as _build_option_position_snapshot_impl
from ._option_positions_state import *
from ._option_positions_state import _reconcile_pending_order_state
from ._option_positions_strategy import *
from ._option_positions_strategy import (
    _exit_should_hold_for_wide_spread,
    _resolve_option_exit_limit_price,
    _round_option_sell_price_to_tick,
    _structured_exit_action,
)
from services.trading_config import WorkerPaths, MomentumHistoryConfig, OptionPositionSettings
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


def _get_underlying_atr_pct(symbol: str, settings: OptionPositionSettings) -> float | None:
    return _get_underlying_atr_pct_impl(
        symbol,
        get_stock_history_client,
        period=settings.volatility_atr_period,
        lookback_days=settings.volatility_atr_lookback_days,
    )


def _build_option_position_snapshot(
    position: Any,
    *,
    settings: OptionPositionSettings,
    momentum_history_config: MomentumHistoryConfig,
    state_path: Path,
    position_state_override: dict[str, Any] | None = None,
    pending_reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _build_option_position_snapshot_impl(
        position,
        settings=settings,
        momentum_history_config=momentum_history_config,
        state_path=state_path,
        get_latest_option_quote=_get_latest_option_quote,
        get_latest_stock_price=_get_latest_stock_price,
        get_underlying_atr_pct=lambda symbol: _get_underlying_atr_pct(symbol, settings),
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
    settings: OptionPositionSettings | None = None,
    state_path: Path | None = None,
    execute_sales: bool = False,
    submit_full_exit_order: Any | None = None,
    submit_partial_exit_order: Any | None = None,
    resolve_order_status: Any | None = None,
    cancel_order: Any | None = None,
    trading_client_override: TradingClient | None = None,
) -> dict[str, Any]:
    """Settings and the state file default to the environment when the caller passes neither."""
    return _manage_current_option_positions_impl(
        company,
        settings=settings or OptionPositionSettings.from_env(),
        state_path=state_path or WorkerPaths.from_env().option_position_state_path,
        execute_sales=execute_sales,
        submit_full_exit_order=submit_full_exit_order,
        submit_partial_exit_order=submit_partial_exit_order,
        resolve_order_status=resolve_order_status,
        cancel_order=cancel_order,
        trading_client_override=trading_client_override,
        get_default_trading_client=get_default_trading_client,
        build_option_position_snapshot=_build_option_position_snapshot,
    )
