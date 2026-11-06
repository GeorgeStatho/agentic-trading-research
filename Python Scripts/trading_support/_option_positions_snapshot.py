from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from services.core.trading_config import MomentumHistoryConfig, OptionPositionSettings
from services.options.option_momentum import evaluate_option_momentum_from_snapshot
from services.options.option_position_state import get_position_state, update_position_state

from ._option_positions_momentum import (
    evaluate_combined_option_momentum,
    evaluate_option_price_momentum,
)
from ._option_positions_market import (
    _days_to_expiration,
    _hours_to_expiration,
    _normalize_pct_ratio,
    _normalize_unrealized_pl_pct,
    _parse_option_symbol,
    _resolve_option_exit_thresholds,
    _resolve_volatility_scale,
)
from ._option_positions_defaults import build_option_exit_rules
from ._option_positions_state import _normalize_order_status, _safe_int
from ._option_positions_strategy import (
    _URGENT_EXIT_REASONS,
    _exit_should_hold_for_wide_spread,
    _resolve_option_exit_limit_price,
    _round_option_sell_price_to_tick,
    _structured_exit_action,
)
from services.runtime.candidate_lifecycle import observe
from services.options.position_exit_policy import build_position_exit_context
from services.core.numbers import safe_float

_TRUSTED_ENTRY_UNDERLYING_PRICE_SOURCES = {
    "broker_fill",
    "historical_backfill",
    "journal",
    "manual",
}


def _build_underlying_momentum_details(
    *,
    position_state: dict[str, Any] | None,
    parsed_symbol: dict[str, Any],
    stock_quote: dict[str, Any],
) -> dict[str, Any]:
    entry_underlying_price = safe_float((position_state or {}).get("entry_underlying_price"))
    entry_underlying_price_source = str(
        (position_state or {}).get("entry_underlying_price_source") or ""
    ).strip().lower()
    if entry_underlying_price is None:
        return {
            "status": "unknown",
            "reasons": [
                "Entry underlying baseline is unavailable; stock-price momentum is informational only."
            ],
        }
    if entry_underlying_price_source not in _TRUSTED_ENTRY_UNDERLYING_PRICE_SOURCES:
        return {
            "status": "unknown",
            "reasons": [
                "Entry underlying baseline is untrusted; stock-price momentum is informational only."
            ],
        }
    momentum_snapshot = {
        "contract_type": parsed_symbol.get("contract_type"),
        "entry_underlying_price": entry_underlying_price,
        "underlying_stock_price": stock_quote.get("price"),
        "strike": parsed_symbol.get("strike"),
    }
    return evaluate_option_momentum_from_snapshot(momentum_snapshot)


def _resolve_exit_rule_days_to_expiration(position_state: dict[str, Any] | None, days_to_expiration: int | None) -> int | None:
    """The DTE a position's exit tuning is chosen from: when it was first managed, not today.

    Re-resolving from the current DTE moved every position into a shorter bucket before its
    own forced-exit DTE arrived, so bucket forced exits never fired.
    """
    entry_dte = _safe_int((position_state or {}).get("entry_dte"))
    return entry_dte if entry_dte is not None else days_to_expiration


def _build_option_position_snapshot(
    position: Any,
    *,
    settings: OptionPositionSettings,
    momentum_history_config: MomentumHistoryConfig,
    state_path: Path,
    get_latest_option_quote: Callable[[str], dict[str, Any]],
    get_latest_stock_price: Callable[[str], dict[str, Any]],
    get_underlying_atr_pct: Callable[[str], float | None] | None = None,
    position_state_override: dict[str, Any] | None = None,
    pending_reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    option_symbol = str(getattr(position, "symbol", "") or "").strip().upper()
    parsed_symbol = _parse_option_symbol(option_symbol)
    option_quote = get_latest_option_quote(option_symbol)
    stock_quote = get_latest_stock_price(parsed_symbol.get("underlying_symbol"))
    entry_price = safe_float(getattr(position, "avg_entry_price", None))
    quantity = safe_float(getattr(position, "qty", None))
    current_bid = option_quote.get("bid_price")
    current_ask = option_quote.get("ask_price")
    current_mid_price = option_quote.get("mid_price")
    expiration_date_text = parsed_symbol.get("expiration_date")
    days_to_expiration = _days_to_expiration(expiration_date_text)
    hours_to_expiration = _hours_to_expiration(expiration_date_text)
    broker_unrealized_pl_pct = safe_float(getattr(position, "unrealized_plpc", None))
    if broker_unrealized_pl_pct is not None:
        broker_unrealized_pl_pct = round(broker_unrealized_pl_pct * 100.0, 4)
    unrealized_pl_pct = _normalize_unrealized_pl_pct(position, entry_price, current_mid_price)
    unrealized_pl_pct_ratio = _normalize_pct_ratio(unrealized_pl_pct)
    broker_unrealized_pl_pct_ratio = _normalize_pct_ratio(broker_unrealized_pl_pct)
    position_state = position_state_override if position_state_override is not None else get_position_state(state_path, option_symbol)
    exit_rule_days_to_expiration = _resolve_exit_rule_days_to_expiration(position_state, days_to_expiration)
    exit_thresholds = _resolve_option_exit_thresholds(
        days_to_expiration=exit_rule_days_to_expiration,
        default_take_profit_pct=settings.take_profit_pct,
        default_stop_loss_pct=settings.stop_loss_pct,
        default_exit_hours_to_expiration=settings.exit_hours_to_expiration,
        exit_rules=build_option_exit_rules(settings.dte_rules),
    )
    option_quote_error = str(option_quote.get("error") or "").strip()
    stock_quote_error = str(stock_quote.get("error") or "").strip()
    quote_errors = [error for error in (option_quote_error, stock_quote_error) if error]
    critical_errors: list[str] = []
    context_notes: list[str] = []
    if option_quote_error:
        if unrealized_pl_pct_ratio is None and hours_to_expiration is None:
            critical_errors.append(option_quote_error)
        else:
            context_notes.append(
                "Option quote was unavailable; the manager used whatever broker P/L and expiration context was still available."
            )
    if stock_quote_error:
        context_notes.append("Underlying stock quote was unavailable; momentum was left informational only.")
    previous_max_pnl_pct = safe_float((position_state or {}).get("max_pnl_pct"))
    current_max_pnl_pct = previous_max_pnl_pct
    if unrealized_pl_pct_ratio is not None:
        current_max_pnl_pct = max(previous_max_pnl_pct or unrealized_pl_pct_ratio, unrealized_pl_pct_ratio)
    underlying_momentum_details = _build_underlying_momentum_details(
        position_state=position_state,
        parsed_symbol=parsed_symbol,
        stock_quote=stock_quote,
    )
    option_price_momentum_details = evaluate_option_price_momentum(
        entry_option_price=entry_price,
        current_option_mid_price=current_mid_price,
        unrealized_pl_ratio=unrealized_pl_pct_ratio,
        max_pnl_pct=current_max_pnl_pct,
        config=settings.price_momentum,
    )
    momentum_details = evaluate_combined_option_momentum(
        option_momentum=option_price_momentum_details,
        underlying_momentum=underlying_momentum_details,
    )
    # The underlying assessment reports price_change_from_entry_pct as a signed
    # percentage where positive = moved in the position's favor (it already
    # negates for puts). Convert it to an adverse move fraction (positive = moved
    # against the position) for the underlying-confirmed stop-loss gate. It is
    # None whenever the entry underlying baseline is missing or untrusted.
    favorable_underlying_move_pct = safe_float(
        underlying_momentum_details.get("price_change_from_entry_pct")
    )
    underlying_adverse_move_pct = (
        -favorable_underlying_move_pct / 100.0
        if favorable_underlying_move_pct is not None
        else None
    )
    broker_quantity = _safe_int(quantity) or 0
    # Volatility-scaled thresholds: widen/tighten the stop-loss, take-profit, and
    # underlying-move gate by the underlying's ATR relative to a baseline. Scale is
    # 1.0 (no change) when disabled or the ATR is unavailable, so the fixed
    # thresholds always remain the safe fallback.
    volatility_scale = 1.0
    underlying_atr_pct: float | None = None
    if settings.volatility_scaled_thresholds and get_underlying_atr_pct is not None:
        try:
            underlying_atr_pct = get_underlying_atr_pct(parsed_symbol.get("underlying_symbol"))
        except Exception:
            underlying_atr_pct = None
        volatility_scale = _resolve_volatility_scale(
            current_atr_pct=underlying_atr_pct,
            baseline_atr_pct=settings.volatility_baseline_atr_pct,
            min_scale=settings.volatility_min_scale,
            max_scale=settings.volatility_max_scale,
        )
    if volatility_scale != 1.0:
        exit_thresholds = replace(
            exit_thresholds,
            stop_loss_pct=round(exit_thresholds.stop_loss_pct * volatility_scale, 4),
            take_profit_pct=round(exit_thresholds.take_profit_pct * volatility_scale, 4),
        )
    effective_stop_loss_underlying_move_pct = round(settings.stop_loss_underlying_move_pct * volatility_scale, 6)
    v2_exit_context = observe(build_position_exit_context, option_symbol, parsed_symbol, stock_quote) or {}
    exit_action, updated_state = _structured_exit_action(
        v2_exit_context=v2_exit_context,
        option_symbol=option_symbol,
        quantity=broker_quantity,
        unrealized_pl_pct_ratio=unrealized_pl_pct_ratio,
        broker_unrealized_pl_pct_ratio=broker_unrealized_pl_pct_ratio,
        exit_rule_days_to_expiration=exit_rule_days_to_expiration,
        hours_to_expiration=hours_to_expiration,
        exit_thresholds=exit_thresholds,
        position_state=position_state,
        critical_errors=critical_errors,
        context_notes=context_notes,
        enable_trailing_profit=settings.enable_trailing_profit,
        trailing_profit_config=settings.trailing,
        momentum_history_config=momentum_history_config,
        momentum_status=str(momentum_details.get("status") or ""),
        momentum_reasons=list(momentum_details.get("reasons") or []),
        option_momentum_status=str(momentum_details.get("option_status") or ""),
        underlying_momentum_status=str(momentum_details.get("underlying_status") or ""),
        momentum_negative_score=safe_float(momentum_details.get("negative_score")),
        recently_filled_order=(
            _normalize_order_status((pending_reconciliation or {}).get("pending_order_status")) == "filled"
        ),
        stop_loss_confirm_checks=settings.stop_loss_confirm_checks,
        underlying_adverse_move_pct=underlying_adverse_move_pct,
        stop_loss_underlying_move_pct=effective_stop_loss_underlying_move_pct,
    )
    # Bid/ask spread as a fraction of the midpoint, used by the liquidity guard
    # and to price limit exits. None when the quote is one-sided/unusable.
    option_spread_pct: float | None = None
    if (
        current_bid is not None
        and current_ask is not None
        and current_mid_price is not None
        and current_mid_price > 0
        and current_ask >= current_bid
    ):
        option_spread_pct = round((current_ask - current_bid) / current_mid_price, 6)
    exit_reason = str(exit_action.get("reason") or "")
    # Liquidity guard: hold a non-urgent exit for another cycle rather than
    # crossing a blown-out spread. Safe to downgrade here because sell-commitment
    # state (e.g. took_first_scale_out) is only persisted at submission time, so
    # the position stays eligible to retry next cycle.
    if exit_action.get("action") in {"sell_full", "sell_partial"} and _exit_should_hold_for_wide_spread(
        exit_reason=exit_reason,
        option_spread_pct=option_spread_pct,
        max_spread_pct=settings.exit_max_spread_pct,
    ):
        updated_state["last_action"] = "hold"
        updated_state["last_decision_reason"] = "liquidity_guard_wide_spread"
        exit_action = {
            **exit_action,
            "action": "hold",
            "reason": "liquidity_guard_wide_spread",
            "sell_fraction": 0.0,
            "qty_to_sell": 0,
            "notes": list(exit_action.get("notes") or [])
            + [
                (
                    f"Exit held: option spread {option_spread_pct * 100.0:.2f}% of mid exceeds the "
                    f"{settings.exit_max_spread_pct * 100.0:.2f}% liquidity ceiling; will retry next cycle."
                )
            ],
        }
        exit_reason = "liquidity_guard_wide_spread"
    # Spread-aware limit price for the exit order (None => manager submits a
    # market order, preserving legacy behavior when limit exits are disabled).
    exit_limit_price: float | None = None
    if settings.exit_use_limit_orders and exit_action.get("action") in {"sell_full", "sell_partial"}:
        last_retryable_exit_reason = str((position_state or {}).get("last_retryable_exit_reason") or "").strip()
        cross_spread = exit_reason in _URGENT_EXIT_REASONS or (
            bool(last_retryable_exit_reason) and last_retryable_exit_reason == exit_reason
        )
        exit_limit_price = _resolve_option_exit_limit_price(
            bid=current_bid,
            ask=current_ask,
            mid=current_mid_price,
            cross_spread=cross_spread,
        )
        exit_limit_price = _round_option_sell_price_to_tick(
            exit_limit_price,
            small_tick=settings.exit_tick_size_small,
            large_tick=settings.exit_tick_size_large,
            tick_price_threshold=settings.exit_tick_price_threshold,
        )
    updated_state.setdefault("entry_underlying_symbol", parsed_symbol.get("underlying_symbol") or "")
    updated_state.setdefault("entry_price", entry_price)
    updated_state.setdefault("entry_qty", broker_quantity)
    existing_entry_underlying_price = safe_float((position_state or {}).get("entry_underlying_price"))
    existing_entry_underlying_price_source = str(
        (position_state or {}).get("entry_underlying_price_source") or ""
    ).strip()
    if existing_entry_underlying_price is not None:
        updated_state.setdefault("entry_underlying_price", existing_entry_underlying_price)
    if existing_entry_underlying_price_source:
        updated_state.setdefault("entry_underlying_price_source", existing_entry_underlying_price_source)
    first_observed_underlying_price = safe_float((position_state or {}).get("first_observed_underlying_price"))
    if first_observed_underlying_price is None and stock_quote.get("price") is not None:
        updated_state["first_observed_underlying_price"] = stock_quote.get("price")
    updated_state.setdefault("entry_dte", days_to_expiration)
    persisted_state = update_position_state(state_path, option_symbol, updated_state)
    return {
        "symbol": option_symbol,
        "underlying_symbol": parsed_symbol.get("underlying_symbol"),
        "contract_type": parsed_symbol.get("contract_type"),
        "entry_price": entry_price,
        "current_bid": current_bid,
        "current_ask": current_ask,
        "mid_price": current_mid_price,
        "expiration_date": expiration_date_text,
        "strike": parsed_symbol.get("strike"),
        "quantity": quantity,
        "unrealized_pl_pct": unrealized_pl_pct,
        "unrealized_pl_ratio": unrealized_pl_pct_ratio,
        "broker_unrealized_pl_pct": broker_unrealized_pl_pct,
        "broker_unrealized_pl_ratio": broker_unrealized_pl_pct_ratio,
        "days_to_expiration": days_to_expiration,
        "hours_to_expiration": hours_to_expiration,
        "exit_rule_days_to_expiration": exit_rule_days_to_expiration,
        "dte_exit_rule": exit_thresholds.dte_rule_label,
        "take_profit_pct": exit_thresholds.take_profit_pct,
        "stop_loss_pct": exit_thresholds.stop_loss_pct,
        "force_exit_days_to_expiration": exit_thresholds.force_exit_days_to_expiration,
        "exit_hours_to_expiration": exit_thresholds.exit_hours_to_expiration,
        "uses_default_exit_thresholds": exit_thresholds.is_default_rule,
        "underlying_stock_price": stock_quote.get("price"),
        "decision": "sell" if exit_action["action"] in {"sell_full", "sell_partial"} else "hold",
        "decision_reasons": exit_action.get("notes", []),
        "exit_action": exit_action.get("action"),
        "exit_reason": exit_action.get("reason"),
        "coactive_exit_reasons": exit_action.get("coactive_exit_reasons", []),
        "v2_exit_comparison": exit_action.get('v2_exit_comparison', {}),
        "implied_volatility": (v2_exit_context.get('inputs') or {}).get('current_iv'),
        "iv_observed_at": (v2_exit_context.get('inputs') or {}).get('iv_at'),
        "qty_to_sell": exit_action.get("qty_to_sell"),
        "sell_fraction": exit_action.get("sell_fraction"),
        "option_spread_pct": option_spread_pct,
        "exit_limit_price": exit_limit_price,
        "volatility_scale": volatility_scale,
        "underlying_atr_pct": underlying_atr_pct,
        "max_pnl_pct": exit_action.get("max_pnl_pct"),
        "profit_protection_active": bool(persisted_state.get("profit_protection_active")),
        "protected_profit_floor_pct": exit_action.get("protected_profit_floor_pct"),
        "trailing_giveback_pct": exit_action.get("trailing_giveback_pct"),
        "took_first_scale_out": bool(persisted_state.get("took_first_scale_out")),
        "took_second_scale_out": bool(persisted_state.get("took_second_scale_out")),
        "pending_order_id": str(persisted_state.get("pending_order_id") or ""),
        "pending_order_status": str((pending_reconciliation or {}).get("pending_order_status") or ""),
        "pending_order_last_checked_at": str((pending_reconciliation or {}).get("pending_order_last_checked_at") or ""),
        "pending_order_terminal": bool((pending_reconciliation or {}).get("pending_order_terminal")),
        "pending_order_error": str((pending_reconciliation or {}).get("pending_order_error") or ""),
        "pending_order_age_seconds": (pending_reconciliation or {}).get("pending_order_age_seconds"),
        "pending_order_stale": bool((pending_reconciliation or {}).get("pending_order_stale")),
        "pending_order_cancel_submitted": bool((pending_reconciliation or {}).get("pending_order_cancel_submitted")),
        "pending_order_filled_qty": int((pending_reconciliation or {}).get("pending_order_filled_qty") or 0),
        "pending_order_remaining_qty": int((pending_reconciliation or {}).get("pending_order_remaining_qty") or 0),
        "last_action": str(persisted_state.get("last_action") or ""),
        "last_action_at": str(persisted_state.get("updated_at") or ""),
        "momentum_status": str(momentum_details.get("status") or ""),
        "momentum_reasons": list(momentum_details.get("reasons") or []),
        "option_price_momentum_status": str(option_price_momentum_details.get("status") or ""),
        "option_price_momentum_reasons": list(option_price_momentum_details.get("reasons") or []),
        "underlying_momentum_status": str(underlying_momentum_details.get("status") or ""),
        "underlying_momentum_reasons": list(underlying_momentum_details.get("reasons") or []),
        "underlying_momentum_informational_only": str(underlying_momentum_details.get("status") or "") == "unknown",
        "underlying_adverse_move_pct": underlying_adverse_move_pct,
        "momentum_tracking_active": bool(persisted_state.get("momentum_tracking_active")),
        "momentum_history_sample_count": int(persisted_state.get("momentum_history_sample_count") or 0),
        "momentum_bad_count": int(persisted_state.get("momentum_bad_count") or 0),
        "momentum_mixed_count": int(persisted_state.get("momentum_mixed_count") or 0),
        "momentum_negative_score": safe_float(persisted_state.get("momentum_negative_score")) or 0.0,
        "momentum_consecutive_bad_count": int(persisted_state.get("momentum_consecutive_bad_count") or 0),
        "trailing_profit_enabled": settings.enable_trailing_profit,
        "trailing_profit_dry_run": settings.trailing_profit_dry_run,
        "option_quote_timestamp": option_quote.get("timestamp"),
        "underlying_quote_timestamp": stock_quote.get("timestamp"),
        "quote_errors": quote_errors,
        "close_submitted": False,
        "close_order_id": "",
        "close_order_status": "",
        "close_order_side": "",
        "close_requested_qty": 0,
        "close_error": "",
    }
