from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from alpaca.trading.client import TradingClient

from services.core.trading_config import MomentumHistoryConfig, OptionPositionSettings
from services.trading.order_values import normalize_order_side, normalize_order_status
from services.options.option_position_state import delete_position_state, get_position_state, load_state

from ._option_positions_market import _is_option_position_symbol
from ._option_positions_state import (
    _mark_exit_submission,
    _reconcile_pending_order_state,
    _safe_int,
)


def _build_momentum_history_config(
    configured: MomentumHistoryConfig,
) -> tuple[MomentumHistoryConfig, list[str]]:
    window_size = max(1, int(configured.window_size))
    min_samples = max(1, min(int(configured.min_samples), window_size))
    bad_count_exit_threshold = max(1, min(int(configured.bad_count_exit_threshold), window_size))
    consecutive_bad_exit_threshold = max(1, min(int(configured.consecutive_bad_exit_threshold), window_size))
    notes: list[str] = []
    if min_samples != int(configured.min_samples):
        notes.append(
            f"Momentum history min_samples was normalized from {int(configured.min_samples)} to {min_samples} to fit within the window size."
        )
    if bad_count_exit_threshold != int(configured.bad_count_exit_threshold):
        notes.append(
            "Momentum history weighted exit threshold was normalized to stay within the configured window size."
        )
    if consecutive_bad_exit_threshold != int(configured.consecutive_bad_exit_threshold):
        notes.append(
            "Momentum history consecutive-bad exit threshold was normalized to stay within the configured window size."
        )
    return (
        MomentumHistoryConfig(
            enable_after_pnl_pct=float(configured.enable_after_pnl_pct),
            window_size=window_size,
            min_samples=min_samples,
            bad_count_exit_threshold=bad_count_exit_threshold,
            consecutive_bad_exit_threshold=consecutive_bad_exit_threshold,
        ),
        notes,
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
                "close_order_status": normalize_order_status(getattr(close_order, "status", "")),
            }
        )
    return close_results


def ManageCurrentOptionPositions(
    company: str | None = None,
    *,
    settings: OptionPositionSettings,
    state_path: Path,
    execute_sales: bool = False,
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
    momentum_history_config, momentum_history_config_notes = _build_momentum_history_config(
        settings.momentum_history
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
            pending_exit_config=settings.pending_exit,
        )
        summary = build_option_position_snapshot(
            position,
            settings=settings,
            momentum_history_config=momentum_history_config,
            state_path=state_path,
            position_state_override=reconciled_state,
            pending_reconciliation=pending_reconciliation,
        )
        if execute_sales and summary["exit_action"] == "sell_full":
            if settings.trailing_profit_dry_run:
                summary["close_error"] = "Dry-run enabled; no sell order was submitted."
            else:
                try:
                    if submit_full_exit_order is not None:
                        close_order = submit_full_exit_order(
                            symbol, limit_price=summary.get("exit_limit_price")
                        )
                    else:
                        legacy_close_order = active_trading_client.close_position(symbol)
                        close_order = {
                            "id": str(getattr(legacy_close_order, "id", "")),
                            "status": normalize_order_status(getattr(legacy_close_order, "status", "")),
                            "side": normalize_order_side(getattr(legacy_close_order, "side", "sell")),
                            "qty": str(getattr(legacy_close_order, "qty", summary.get("quantity") or "")),
                            "submitted_at": str(getattr(legacy_close_order, "submitted_at", "")),
                        }
                    summary["close_submitted"] = True
                    summary["close_order_id"] = str(close_order.get("id") or "")
                    summary["close_order_status"] = normalize_order_status(close_order.get("status"))
                    summary["close_order_side"] = normalize_order_side(close_order.get("side"))
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
            if settings.trailing_profit_dry_run:
                summary["close_error"] = "Dry-run enabled; partial sell was not submitted."
            elif submit_partial_exit_order is None:
                summary["close_error"] = "Partial sell action computed by structured engine, but no partial option sell callback was provided."
            else:
                try:
                    close_order = submit_partial_exit_order(
                        symbol,
                        int(summary.get("qty_to_sell") or 0),
                        limit_price=summary.get("exit_limit_price"),
                    )
                    summary["close_submitted"] = True
                    summary["close_order_id"] = str(close_order.get("id") or "")
                    summary["close_order_status"] = normalize_order_status(close_order.get("status"))
                    summary["close_order_side"] = normalize_order_side(close_order.get("side"))
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
    # A contract in the state file was held on an earlier cycle; gone now, the position is flat.
    stale_symbols: set[str] = set()
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
        "trailing_profit_enabled": settings.enable_trailing_profit,
        "trailing_profit_dry_run": settings.trailing_profit_dry_run,
        "option_position_state_path": str(state_path),
        "uses_dte_based_exit_thresholds": True,
        "take_profit_pct": settings.take_profit_pct,
        "stop_loss_pct": settings.stop_loss_pct,
        "stop_loss_confirm_checks": settings.stop_loss_confirm_checks,
        "stop_loss_underlying_move_pct": settings.stop_loss_underlying_move_pct,
        "exit_use_limit_orders": settings.exit_use_limit_orders,
        "exit_max_spread_pct": settings.exit_max_spread_pct,
        "volatility_scaled_thresholds": settings.volatility_scaled_thresholds,
        "volatility_baseline_atr_pct": settings.volatility_baseline_atr_pct,
        "exit_hours_to_expiration": settings.exit_hours_to_expiration,
        "position_count": len(position_summaries),
        "sell_count": sum(1 for summary in position_summaries if summary.get("exit_action") in {"sell_full", "sell_partial"}),
        "full_sell_count": sum(1 for summary in position_summaries if summary.get("exit_action") == "sell_full"),
        "partial_sell_count": sum(1 for summary in position_summaries if summary.get("exit_action") == "sell_partial"),
        "close_submitted_count": sum(1 for summary in position_summaries if summary.get("close_submitted")),
        "flattened_symbols": sorted(stale_symbols),
        "momentum_history_config_notes": momentum_history_config_notes,
        "positions": position_summaries,
    }
