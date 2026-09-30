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
from services.position_exit_policy import shadow_exit_comparison


def _deterministic_option_exit_decision(
    *,
    unrealized_pl_pct: float | None,
    stop_loss_unrealized_pl_pct: float | None,
    hours_to_expiration: float | None,
    take_profit_pct: float,
    stop_loss_pct: float,
    exit_hours_to_expiration: float,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if unrealized_pl_pct is not None and unrealized_pl_pct >= take_profit_pct:
        reasons.append(f"Take-profit triggered at {unrealized_pl_pct:.2f}% versus target {take_profit_pct:.2f}%.")
    if stop_loss_unrealized_pl_pct is not None and stop_loss_unrealized_pl_pct <= stop_loss_pct:
        reasons.append(
            f"Stop-loss triggered at {stop_loss_unrealized_pl_pct:.2f}% versus floor {stop_loss_pct:.2f}%."
        )
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
    if quantity <= 1 or sell_fraction <= 0:
        return 0
    requested_qty = int(round(quantity * sell_fraction))
    return max(1, min(quantity, requested_qty))


# Hard risk exits that must get out this cycle: they bypass the wide-spread
# liquidity guard and cross straight to the bid when priced as a limit order.
_URGENT_EXIT_REASONS = frozenset({"stop_loss", "near_expiration"})


def _exit_should_hold_for_wide_spread(
    *,
    exit_reason: str,
    option_spread_pct: float | None,
    max_spread_pct: float,
) -> bool:
    """Return True when a non-urgent exit should be held because the option's
    bid/ask spread is wider than the configured ceiling. Urgent exits and an
    unmeasurable/disabled spread never block."""
    if max_spread_pct <= 0.0:
        return False
    if exit_reason in _URGENT_EXIT_REASONS:
        return False
    if option_spread_pct is None:
        return False
    return option_spread_pct > max_spread_pct


def _resolve_option_exit_limit_price(
    *,
    bid: float | None,
    ask: float | None,
    mid: float | None,
    cross_spread: bool,
) -> float | None:
    """Compute a spread-aware limit price for a sell exit. Returns None when the
    quote is unusable so the caller can fall back to a market order.

    - cross_spread True  -> marketable limit at the bid (near-certain fill, but
      never sells *through* the bid the way a bare market order can on an
      illiquid book).
    - cross_spread False -> passive limit at the midpoint to try to capture the
      spread, clamped so it is never posted below the bid.
    """
    bid_value = safe_float(bid)
    if bid_value is None or bid_value <= 0:
        return None
    if cross_spread:
        return round(bid_value, 2)
    mid_value = safe_float(mid)
    ask_value = safe_float(ask)
    if mid_value is not None and mid_value > 0:
        target = mid_value
    elif ask_value is not None and ask_value > 0:
        target = (bid_value + ask_value) / 2.0
    else:
        return None
    return round(max(bid_value, target), 2)


def _round_option_sell_price_to_tick(
    price: float | None,
    *,
    small_tick: float = 0.05,
    large_tick: float = 0.10,
    tick_price_threshold: float = 3.0,
) -> float | None:
    """Round a sell-limit price DOWN to a broker-valid tick. Rounding down keeps
    the order valid and marketable (a sell limit still fills at the NBBO bid), so
    crossing exits are unaffected and passive exits just get slightly more
    fillable. Prices below one tick fall back to a penny to avoid rounding to 0."""
    price_value = safe_float(price)
    if price_value is None or price_value <= 0:
        return price
    tick = large_tick if price_value >= tick_price_threshold else small_tick
    tick_value = safe_float(tick)
    if tick_value is None or tick_value <= 0:
        return round(price_value, 2)
    tick_cents = max(1, int(round(tick_value * 100)))
    price_cents = int(round(price_value * 100))
    floored_cents = (price_cents // tick_cents) * tick_cents
    if floored_cents <= 0:
        return round(price_value, 2)
    return round(floored_cents / 100.0, 2)


def _structured_exit_action_impl(
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
    broker_unrealized_pl_pct_ratio: float | None = None,
    stop_loss_confirm_checks: int = 1,
    underlying_adverse_move_pct: float | None = None,
    stop_loss_underlying_move_pct: float = 0.0,
    ignored_exit_reasons: frozenset[str] = frozenset(),
    v2_exit_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    existing_state = dict(position_state or {})
    notes: list[str] = list(context_notes)
    profit_unrealized_pl_pct_ratio = (
        broker_unrealized_pl_pct_ratio
        if broker_unrealized_pl_pct_ratio is not None
        else unrealized_pl_pct_ratio
    )
    updated_state: dict[str, Any] = {
        **existing_state,
        "entry_option_symbol": option_symbol,
        "remaining_qty": quantity,
        "last_seen_qty": quantity,
    }
    previous_max_pnl_pct = safe_float(existing_state.get("max_pnl_pct"))
    current_max_pnl_pct = previous_max_pnl_pct
    if profit_unrealized_pl_pct_ratio is not None:
        current_max_pnl_pct = max(
            previous_max_pnl_pct or profit_unrealized_pl_pct_ratio,
            profit_unrealized_pl_pct_ratio,
        )
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
            unrealized_pl_pct_ratio=profit_unrealized_pl_pct_ratio,
            momentum_status=momentum_status,
            option_momentum_status=option_momentum_status,
            underlying_momentum_status=underlying_momentum_status,
            momentum_negative_score=momentum_negative_score,
            momentum_history_config=momentum_history_config,
        )
    )
    if broker_unrealized_pl_pct_ratio is not None:
        if "stop_loss" not in ignored_exit_reasons and broker_unrealized_pl_pct_ratio <= (exit_thresholds.stop_loss_pct / 100.0):
            # Gate the premium stop on a real adverse move in the underlying.
            # Option premium is leveraged and spread/IV-noisy, so a small stock
            # wobble can breach the floor and bounce back. When configured, hold
            # through the breach unless the underlying itself has moved against the
            # position by at least the threshold. If the underlying move is
            # unknown/untrusted we fall back to the premium stop so protection is
            # never silently lost.
            require_underlying_move = stop_loss_underlying_move_pct > 0.0
            if (
                require_underlying_move
                and underlying_adverse_move_pct is not None
                and underlying_adverse_move_pct < stop_loss_underlying_move_pct
            ):
                updated_state["stop_loss_breach_count"] = 0
                updated_state["last_action"] = "hold"
                updated_state["last_decision_reason"] = "stop_loss_underlying_hold"
                return (
                    _build_exit_action(
                        action="hold",
                        reason="stop_loss_underlying_hold",
                        pnl_pct=broker_unrealized_pl_pct_ratio,
                        max_pnl_pct=current_max_pnl_pct,
                        protected_profit_floor_pct=protected_profit_floor_pct,
                        trailing_giveback_pct=trailing_giveback_pct,
                        notes=notes + [
                            (
                                f"Broker P/L {broker_unrealized_pl_pct_ratio * 100.0:.2f}% breached the "
                                f"{exit_thresholds.stop_loss_pct:.2f}% floor, but the underlying only moved "
                                f"{underlying_adverse_move_pct * 100.0:.2f}% against the position versus the "
                                f"{stop_loss_underlying_move_pct * 100.0:.2f}% threshold; holding through option-premium noise."
                            )
                        ],
                    ),
                    updated_state,
                )
            required_stop_loss_breaches = max(1, int(stop_loss_confirm_checks))
            stop_loss_breach_count = (
                int(safe_float(existing_state.get("stop_loss_breach_count")) or 0) + 1
            )
            updated_state["stop_loss_breach_count"] = stop_loss_breach_count
            if stop_loss_breach_count >= required_stop_loss_breaches:
                updated_state["last_action"] = "sell_full"
                updated_state["last_decision_reason"] = "stop_loss"
                confirmation_note = (
                    f" (confirmed on {stop_loss_breach_count} consecutive checks)"
                    if required_stop_loss_breaches > 1
                    else ""
                )
                return (
                    _build_exit_action(
                        action="sell_full",
                        reason="stop_loss",
                        pnl_pct=broker_unrealized_pl_pct_ratio,
                        max_pnl_pct=current_max_pnl_pct,
                        protected_profit_floor_pct=protected_profit_floor_pct,
                        trailing_giveback_pct=trailing_giveback_pct,
                        sell_fraction=1.0,
                        qty_to_sell=quantity,
                        notes=notes + [
                            (
                                f"Stop-loss triggered at {broker_unrealized_pl_pct_ratio * 100.0:.2f}% "
                                f"versus floor {exit_thresholds.stop_loss_pct:.2f}% based on broker P/L"
                                f"{confirmation_note}."
                            )
                        ],
                    ),
                    updated_state,
                )
            updated_state["last_action"] = "hold"
            updated_state["last_decision_reason"] = "stop_loss_pending_confirmation"
            return (
                _build_exit_action(
                    action="hold",
                    reason="stop_loss_pending_confirmation",
                    pnl_pct=broker_unrealized_pl_pct_ratio,
                    max_pnl_pct=current_max_pnl_pct,
                    protected_profit_floor_pct=protected_profit_floor_pct,
                    trailing_giveback_pct=trailing_giveback_pct,
                    notes=notes + [
                        (
                            f"Stop-loss breach {stop_loss_breach_count} of {required_stop_loss_breaches} "
                            f"at {broker_unrealized_pl_pct_ratio * 100.0:.2f}% versus floor "
                            f"{exit_thresholds.stop_loss_pct:.2f}%; holding for confirmation before a full exit."
                        )
                    ],
                ),
                updated_state,
            )
        # Broker P/L recovered above the stop-loss floor; clear any pending breach
        # streak so a fresh dip must re-confirm from scratch.
        updated_state["stop_loss_breach_count"] = 0
    def v2_action(reason):
        updated_state['last_action'] = 'sell_full'
        updated_state['last_decision_reason'] = reason
        return (_build_exit_action(action='sell_full', reason=reason, pnl_pct=profit_unrealized_pl_pct_ratio,
            max_pnl_pct=current_max_pnl_pct, protected_profit_floor_pct=protected_profit_floor_pct,
            trailing_giveback_pct=trailing_giveback_pct, sell_fraction=1.0, qty_to_sell=quantity,
            notes=notes + [reason + ': deterministic V2 position rule.']), updated_state)

    active_v2 = set((v2_exit_context or {}).get('active_reasons') or []) - ignored_exit_reasons
    if 'CATALYST_EXPIRED' in active_v2:
        return v2_action('CATALYST_EXPIRED')
    if "near_expiration" not in ignored_exit_reasons and hours_to_expiration is not None and hours_to_expiration <= exit_thresholds.exit_hours_to_expiration:
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
    if 'VOLATILITY_REGIME_CHANGE' in active_v2:
        return v2_action('VOLATILITY_REGIME_CHANGE')
    if not enable_trailing_profit:
        decision, reasons = _deterministic_option_exit_decision(
            unrealized_pl_pct=(
                profit_unrealized_pl_pct_ratio * 100.0
                if profit_unrealized_pl_pct_ratio is not None and "static_take_profit" not in ignored_exit_reasons
                else None
            ),
            stop_loss_unrealized_pl_pct=(
                broker_unrealized_pl_pct_ratio * 100.0
                if broker_unrealized_pl_pct_ratio is not None and "stop_loss" not in ignored_exit_reasons
                else None
            ),
            hours_to_expiration=None if "near_expiration" in ignored_exit_reasons else hours_to_expiration,
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
                pnl_pct=profit_unrealized_pl_pct_ratio,
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
    if (
        current_max_pnl_pct is not None
        and current_max_pnl_pct >= trailing_profit_config.dynamic_giveback_start_pct
    ):
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
        "trailing_profit_stop" not in ignored_exit_reasons
        and profit_protection_active
        and profit_unrealized_pl_pct_ratio is not None
        and protected_profit_floor_pct is not None
        and profit_unrealized_pl_pct_ratio <= protected_profit_floor_pct
    ):
        updated_state["last_action"] = "sell_full"
        updated_state["last_decision_reason"] = "trailing_profit_stop"
        return (
            _build_exit_action(
                action="sell_full",
                reason="trailing_profit_stop",
                pnl_pct=profit_unrealized_pl_pct_ratio,
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
    if "momentum_history_failed" not in ignored_exit_reasons and trailing_profit_config.enable_momentum_exit and momentum_history_exit_triggered:
        updated_state["last_action"] = "sell_full"
        updated_state["last_decision_reason"] = "momentum_history_failed"
        return (
            _build_exit_action(
                action="sell_full",
                reason="momentum_history_failed",
                pnl_pct=profit_unrealized_pl_pct_ratio,
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
        profit_unrealized_pl_pct_ratio is not None
        and profit_unrealized_pl_pct_ratio >= trailing_profit_config.first_scale_out_trigger_pct
        and not took_first_scale_out
        and "first_scale_out_trigger" not in ignored_exit_reasons
    ):
        qty_to_sell = _compute_scaled_sell_qty(quantity, trailing_profit_config.first_scale_out_fraction)
        if qty_to_sell <= 0:
            notes.append("First scale-out trigger was reached, but the remaining quantity was too small to sell a partial lot.")
        else:
            action = "sell_full" if qty_to_sell >= quantity else "sell_partial"
            updated_state["last_action"] = action
            updated_state["last_decision_reason"] = "first_scale_out_trigger"
            return (
                _build_exit_action(
                    action=action,
                    reason="first_scale_out_trigger",
                    pnl_pct=profit_unrealized_pl_pct_ratio,
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
        profit_unrealized_pl_pct_ratio is not None
        and profit_unrealized_pl_pct_ratio >= trailing_profit_config.second_scale_out_trigger_pct
        and not took_second_scale_out
        and "second_scale_out_trigger" not in ignored_exit_reasons
    ):
        qty_to_sell = _compute_scaled_sell_qty(quantity, trailing_profit_config.second_scale_out_fraction)
        if qty_to_sell <= 0:
            notes.append("Second scale-out trigger was reached, but the remaining quantity was too small to sell a partial lot.")
        else:
            action = "sell_full" if qty_to_sell >= quantity else "sell_partial"
            updated_state["last_action"] = action
            updated_state["last_decision_reason"] = "second_scale_out_trigger"
            return (
                _build_exit_action(
                    action=action,
                    reason="second_scale_out_trigger",
                    pnl_pct=profit_unrealized_pl_pct_ratio,
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


def _structured_exit_action(**kwargs):
    """Retain the priority verdict; evaluate lower rungs on disposable state for attribution."""
    action, state = _structured_exit_action_impl(**kwargs)
    coactive = []
    probe = action
    for _ in range(10):
        if probe.get("action") not in {"sell_full", "sell_partial"}:
            break
        reason = probe["reason"]
        if reason in coactive:
            break
        coactive.append(reason)
        probe, _ = _structured_exit_action_impl(**kwargs, ignored_exit_reasons=frozenset(coactive))
    action["coactive_exit_reasons"] = coactive
    if kwargs.get('v2_exit_context') is not None:
        action['v2_exit_comparison'] = shadow_exit_comparison(action, kwargs['v2_exit_context'])
    return action, state
