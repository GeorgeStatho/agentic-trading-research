from __future__ import annotations

from typing import Any

from ._option_positions_defaults import ExitThresholds, MomentumHistoryConfig, TrailingProfitConfig
from ._option_positions_market import _resolve_trailing_giveback_pct
from ._option_positions_state import (
    _build_momentum_history_updates,
    _coerce_bool,
    _evaluate_momentum_history_exit,
)
from .utils import safe_float


def _deterministic_option_exit_decision(
    *,
    unrealized_pl_pct: float | None,
    hours_to_expiration: float | None,
    take_profit_pct: float,
    stop_loss_pct: float,
    exit_hours_to_expiration: float,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if unrealized_pl_pct is not None and unrealized_pl_pct >= take_profit_pct:
        reasons.append(f"Take-profit triggered at {unrealized_pl_pct:.2f}% versus target {take_profit_pct:.2f}%.")
    if unrealized_pl_pct is not None and unrealized_pl_pct <= stop_loss_pct:
        reasons.append(f"Stop-loss triggered at {unrealized_pl_pct:.2f}% versus floor {stop_loss_pct:.2f}%.")
    if hours_to_expiration is not None and hours_to_expiration <= exit_hours_to_expiration:
        reasons.append(
            f"Hours to expiration is {hours_to_expiration:.2f}, at or below exit threshold {exit_hours_to_expiration:.2f}."
        )
    return ("sell", reasons) if reasons else ("hold", ["No deterministic sell rule was triggered."])


def _build_exit_action(
    *,
    action: str,
    reason: str,
    pnl_pct: float | None,
    max_pnl_pct: float | None,
    protected_profit_floor_pct: float | None,
    trailing_giveback_pct: float | None,
    sell_fraction: float = 0.0,
    qty_to_sell: int = 0,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "reason": reason,
        "sell_fraction": round(float(sell_fraction), 6) if sell_fraction else 0.0,
        "qty_to_sell": max(0, int(qty_to_sell)),
        "pnl_pct": pnl_pct,
        "max_pnl_pct": max_pnl_pct,
        "protected_profit_floor_pct": protected_profit_floor_pct,
        "trailing_giveback_pct": trailing_giveback_pct,
        "notes": notes or [],
    }


def _compute_scaled_sell_qty(quantity: int, sell_fraction: float) -> int:
    if quantity <= 0 or sell_fraction <= 0:
        return 0
    requested_qty = int(round(quantity * sell_fraction))
    return max(1, min(quantity, requested_qty))


def _structured_exit_action(
    *,
    option_symbol: str,
    quantity: int,
    unrealized_pl_pct_ratio: float | None,
    days_to_expiration: int | None,
    hours_to_expiration: float | None,
    exit_thresholds: ExitThresholds,
    position_state: dict[str, Any] | None,
    critical_errors: list[str],
    context_notes: list[str],
    enable_trailing_profit: bool,
    trailing_profit_config: TrailingProfitConfig,
    momentum_history_config: MomentumHistoryConfig,
    momentum_status: str,
    momentum_reasons: list[str],
    recently_filled_order: bool,
    option_momentum_status: str = "unknown",
    underlying_momentum_status: str = "unknown",
    momentum_negative_score: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    existing_state = dict(position_state or {})
    notes: list[str] = list(context_notes)
    updated_state: dict[str, Any] = {
        **existing_state,
        "entry_option_symbol": option_symbol,
        "remaining_qty": quantity,
        "last_seen_qty": quantity,
    }
    previous_max_pnl_pct = safe_float(existing_state.get("max_pnl_pct"))
    current_max_pnl_pct = previous_max_pnl_pct
    if unrealized_pl_pct_ratio is not None:
        current_max_pnl_pct = max(previous_max_pnl_pct or unrealized_pl_pct_ratio, unrealized_pl_pct_ratio)
    updated_state["max_pnl_pct"] = current_max_pnl_pct
    pending_order_id = str(existing_state.get("pending_order_id") or "").strip()
    protected_profit_floor_pct = safe_float(existing_state.get("protected_profit_floor_pct"))
    trailing_giveback_pct = safe_float(existing_state.get("trailing_giveback_pct"))
    took_first_scale_out = _coerce_bool(existing_state.get("took_first_scale_out"), False)
    took_second_scale_out = _coerce_bool(existing_state.get("took_second_scale_out"), False)
    profit_protection_active = _coerce_bool(existing_state.get("profit_protection_active"), False)
    if critical_errors:
        notes.extend(critical_errors)
        updated_state["last_action"] = "hold"
        updated_state["last_decision_reason"] = "data_invalid"
        return (
            _build_exit_action(
                action="hold",
                reason="data_invalid",
                pnl_pct=unrealized_pl_pct_ratio,
                max_pnl_pct=current_max_pnl_pct,
                protected_profit_floor_pct=protected_profit_floor_pct,
                trailing_giveback_pct=trailing_giveback_pct,
                notes=notes,
            ),
            updated_state,
        )
    if quantity <= 0:
        updated_state["last_action"] = "hold"
        updated_state["last_decision_reason"] = "no_position_qty"
        return (
            _build_exit_action(
                action="hold",
                reason="no_position_qty",
                pnl_pct=unrealized_pl_pct_ratio,
                max_pnl_pct=current_max_pnl_pct,
                protected_profit_floor_pct=protected_profit_floor_pct,
                trailing_giveback_pct=trailing_giveback_pct,
                notes=notes,
            ),
            updated_state,
        )
    if recently_filled_order:
        updated_state["last_action"] = "hold"
        updated_state["last_decision_reason"] = "recent_fill_reconciliation"
        return (
            _build_exit_action(
                action="hold",
                reason="recent_fill_reconciliation",
                pnl_pct=unrealized_pl_pct_ratio,
                max_pnl_pct=current_max_pnl_pct,
                protected_profit_floor_pct=protected_profit_floor_pct,
                trailing_giveback_pct=trailing_giveback_pct,
                notes=["A pending exit order was just reconciled as filled; waiting for broker position state to catch up."],
            ),
            updated_state,
        )
    if pending_order_id:
        notes.append(f"Pending order {pending_order_id} exists.")
        updated_state["last_action"] = "hold"
        updated_state["last_decision_reason"] = "pending_exit_order"
        return (
            _build_exit_action(
                action="hold",
                reason="pending_exit_order",
                pnl_pct=unrealized_pl_pct_ratio,
                max_pnl_pct=current_max_pnl_pct,
                protected_profit_floor_pct=protected_profit_floor_pct,
                trailing_giveback_pct=trailing_giveback_pct,
                notes=notes,
            ),
            updated_state,
        )
    updated_state.update(
        _build_momentum_history_updates(
            existing_state=existing_state,
            current_max_pnl_pct=current_max_pnl_pct,
            unrealized_pl_pct_ratio=unrealized_pl_pct_ratio,
            momentum_status=momentum_status,
            option_momentum_status=option_momentum_status,
            underlying_momentum_status=underlying_momentum_status,
            momentum_negative_score=momentum_negative_score,
            momentum_history_config=momentum_history_config,
        )
    )
    if unrealized_pl_pct_ratio is not None and unrealized_pl_pct_ratio <= (exit_thresholds.stop_loss_pct / 100.0):
        updated_state["last_action"] = "sell_full"
        updated_state["last_decision_reason"] = "stop_loss"
        return (
            _build_exit_action(
                action="sell_full",
                reason="stop_loss",
                pnl_pct=unrealized_pl_pct_ratio,
                max_pnl_pct=current_max_pnl_pct,
                protected_profit_floor_pct=protected_profit_floor_pct,
                trailing_giveback_pct=trailing_giveback_pct,
                sell_fraction=1.0,
                qty_to_sell=quantity,
                notes=notes + [f"Stop-loss triggered at {unrealized_pl_pct_ratio * 100.0:.2f}% versus floor {exit_thresholds.stop_loss_pct:.2f}%."],
            ),
            updated_state,
        )
    if hours_to_expiration is not None and hours_to_expiration <= exit_thresholds.exit_hours_to_expiration:
        updated_state["last_action"] = "sell_full"
        updated_state["last_decision_reason"] = "near_expiration"
        return (
            _build_exit_action(
                action="sell_full",
                reason="near_expiration",
                pnl_pct=unrealized_pl_pct_ratio,
                max_pnl_pct=current_max_pnl_pct,
                protected_profit_floor_pct=protected_profit_floor_pct,
                trailing_giveback_pct=trailing_giveback_pct,
                sell_fraction=1.0,
                qty_to_sell=quantity,
                notes=notes + [f"Hours to expiration is {hours_to_expiration:.2f}, at or below exit threshold {exit_thresholds.exit_hours_to_expiration:.2f}."],
            ),
            updated_state,
        )
    if not enable_trailing_profit:
        decision, reasons = _deterministic_option_exit_decision(
            unrealized_pl_pct=(unrealized_pl_pct_ratio * 100.0) if unrealized_pl_pct_ratio is not None else None,
            hours_to_expiration=hours_to_expiration,
            take_profit_pct=exit_thresholds.take_profit_pct,
            stop_loss_pct=exit_thresholds.stop_loss_pct,
            exit_hours_to_expiration=exit_thresholds.exit_hours_to_expiration,
        )
        updated_state["last_action"] = "sell_full" if decision == "sell" else "hold"
        updated_state["last_decision_reason"] = "static_exit" if decision == "sell" else "hold"
        return (
            _build_exit_action(
                action="sell_full" if decision == "sell" else "hold",
                reason="static_take_profit" if decision == "sell" else "hold",
                pnl_pct=unrealized_pl_pct_ratio,
                max_pnl_pct=current_max_pnl_pct,
                protected_profit_floor_pct=protected_profit_floor_pct,
                trailing_giveback_pct=trailing_giveback_pct,
                sell_fraction=1.0 if decision == "sell" else 0.0,
                qty_to_sell=quantity if decision == "sell" else 0,
                notes=notes + reasons,
            ),
            updated_state,
        )
    if current_max_pnl_pct is not None and current_max_pnl_pct >= trailing_profit_config.protection_trigger_pct:
        profit_protection_active = True
        protected_profit_floor_pct = max(protected_profit_floor_pct or 0.0, trailing_profit_config.initial_floor_pct)
        notes.append("Profit protection activated.")
    trailing_giveback_pct = _resolve_trailing_giveback_pct(days_to_expiration, trailing_profit_config)
    updated_state["trailing_giveback_pct"] = trailing_giveback_pct
    if current_max_pnl_pct is not None and current_max_pnl_pct >= trailing_profit_config.first_scale_out_trigger_pct:
        profit_protection_active = True
        protected_profit_floor_pct = max(
            protected_profit_floor_pct or 0.0,
            max(0.25, current_max_pnl_pct - trailing_giveback_pct),
        )
    if current_max_pnl_pct is not None and current_max_pnl_pct >= 1.00:
        protected_profit_floor_pct = max(protected_profit_floor_pct or 0.0, trailing_profit_config.floor_100_pct)
    if current_max_pnl_pct is not None and current_max_pnl_pct >= 1.50:
        protected_profit_floor_pct = max(protected_profit_floor_pct or 0.0, trailing_profit_config.floor_150_pct)
    if current_max_pnl_pct is not None and current_max_pnl_pct >= 2.00:
        protected_profit_floor_pct = max(protected_profit_floor_pct or 0.0, trailing_profit_config.floor_200_pct)
    updated_state["profit_protection_active"] = profit_protection_active
    updated_state["protected_profit_floor_pct"] = protected_profit_floor_pct
    updated_state["took_first_scale_out"] = took_first_scale_out
    updated_state["took_second_scale_out"] = took_second_scale_out
    if (
        profit_protection_active
        and unrealized_pl_pct_ratio is not None
        and protected_profit_floor_pct is not None
        and unrealized_pl_pct_ratio <= protected_profit_floor_pct
    ):
        updated_state["last_action"] = "sell_full"
        updated_state["last_decision_reason"] = "trailing_profit_stop"
        return (
            _build_exit_action(
                action="sell_full",
                reason="trailing_profit_stop",
                pnl_pct=unrealized_pl_pct_ratio,
                max_pnl_pct=current_max_pnl_pct,
                protected_profit_floor_pct=protected_profit_floor_pct,
                trailing_giveback_pct=trailing_giveback_pct,
                sell_fraction=1.0,
                qty_to_sell=quantity,
                notes=notes,
            ),
            updated_state,
        )
    momentum_history_exit_triggered, momentum_history_notes = _evaluate_momentum_history_exit(
        state=updated_state,
        momentum_history_config=momentum_history_config,
    )
    if trailing_profit_config.enable_momentum_exit and momentum_history_exit_triggered:
        updated_state["last_action"] = "sell_full"
        updated_state["last_decision_reason"] = "momentum_history_failed"
        return (
            _build_exit_action(
                action="sell_full",
                reason="momentum_history_failed",
                pnl_pct=unrealized_pl_pct_ratio,
                max_pnl_pct=current_max_pnl_pct,
                protected_profit_floor_pct=protected_profit_floor_pct,
                trailing_giveback_pct=trailing_giveback_pct,
                sell_fraction=1.0,
                qty_to_sell=quantity,
                notes=notes + momentum_reasons + momentum_history_notes,
            ),
            updated_state,
        )
    if (
        unrealized_pl_pct_ratio is not None
        and unrealized_pl_pct_ratio >= trailing_profit_config.first_scale_out_trigger_pct
        and not took_first_scale_out
    ):
        qty_to_sell = _compute_scaled_sell_qty(quantity, trailing_profit_config.first_scale_out_fraction)
        action = "sell_full" if qty_to_sell >= quantity else "sell_partial"
        updated_state["last_action"] = action
        updated_state["last_decision_reason"] = "first_scale_out_trigger"
        return (
            _build_exit_action(
                action=action,
                reason="first_scale_out_trigger",
                pnl_pct=unrealized_pl_pct_ratio,
                max_pnl_pct=current_max_pnl_pct,
                protected_profit_floor_pct=protected_profit_floor_pct,
                trailing_giveback_pct=trailing_giveback_pct,
                sell_fraction=1.0 if action == "sell_full" else trailing_profit_config.first_scale_out_fraction,
                qty_to_sell=qty_to_sell,
                notes=notes,
            ),
            updated_state,
        )
    if (
        unrealized_pl_pct_ratio is not None
        and unrealized_pl_pct_ratio >= trailing_profit_config.second_scale_out_trigger_pct
        and not took_second_scale_out
    ):
        qty_to_sell = _compute_scaled_sell_qty(quantity, trailing_profit_config.second_scale_out_fraction)
        action = "sell_full" if qty_to_sell >= quantity else "sell_partial"
        updated_state["last_action"] = action
        updated_state["last_decision_reason"] = "second_scale_out_trigger"
        return (
            _build_exit_action(
                action=action,
                reason="second_scale_out_trigger",
                pnl_pct=unrealized_pl_pct_ratio,
                max_pnl_pct=current_max_pnl_pct,
                protected_profit_floor_pct=protected_profit_floor_pct,
                trailing_giveback_pct=trailing_giveback_pct,
                sell_fraction=1.0 if action == "sell_full" else trailing_profit_config.second_scale_out_fraction,
                qty_to_sell=qty_to_sell,
                notes=notes,
            ),
            updated_state,
        )
    updated_state["last_action"] = "hold"
    updated_state["last_decision_reason"] = "hold"
    return (
        _build_exit_action(
            action="hold",
            reason="hold",
            pnl_pct=unrealized_pl_pct_ratio,
            max_pnl_pct=current_max_pnl_pct,
            protected_profit_floor_pct=protected_profit_floor_pct,
            trailing_giveback_pct=trailing_giveback_pct,
            notes=notes or ["No structured exit rule was triggered."],
        ),
        updated_state,
    )
