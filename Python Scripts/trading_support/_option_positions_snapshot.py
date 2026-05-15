from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from services.option_momentum import evaluate_option_momentum_from_snapshot
from services.option_position_state import get_position_state, update_position_state

from ._option_positions_momentum import (
    MOMENTUM_MIXED,
    evaluate_combined_option_momentum,
    evaluate_option_price_momentum,
)
from ._option_positions_market import (
    _days_to_expiration,
    _get_latest_stock_price,
    _get_latest_option_quote,
    _hours_to_expiration,
    _normalize_pct_ratio,
    _normalize_unrealized_pl_pct,
    _parse_option_symbol,
    _resolve_option_exit_thresholds,
)
from ._option_positions_state import _normalize_order_status, _safe_int
from ._option_positions_strategy import _structured_exit_action
from .utils import safe_float


def _build_option_position_snapshot(
    position: Any,
    *,
    take_profit_pct: float,
    stop_loss_pct: float,
    exit_hours_to_expiration: float,
    enable_trailing_profit: bool,
    trailing_profit_dry_run: bool,
    trailing_profit_config: Any,
    momentum_history_config: Any,
    state_path: Path,
    get_latest_option_quote: Callable[[str], dict[str, Any]],
    get_latest_stock_price: Callable[[str], dict[str, Any]],
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
    unrealized_pl_pct = _normalize_unrealized_pl_pct(position, entry_price, current_mid_price)
    unrealized_pl_pct_ratio = _normalize_pct_ratio(unrealized_pl_pct)
    exit_thresholds = _resolve_option_exit_thresholds(
        days_to_expiration=days_to_expiration,
        default_take_profit_pct=take_profit_pct,
        default_stop_loss_pct=stop_loss_pct,
        default_exit_hours_to_expiration=exit_hours_to_expiration,
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
    position_state = position_state_override if position_state_override is not None else get_position_state(state_path, option_symbol)
    previous_max_pnl_pct = safe_float((position_state or {}).get("max_pnl_pct"))
    current_max_pnl_pct = previous_max_pnl_pct
    if unrealized_pl_pct_ratio is not None:
        current_max_pnl_pct = max(previous_max_pnl_pct or unrealized_pl_pct_ratio, unrealized_pl_pct_ratio)
    momentum_snapshot = {
        "contract_type": parsed_symbol.get("contract_type"),
        "entry_underlying_price": (position_state or {}).get("entry_underlying_price"),
        "underlying_stock_price": stock_quote.get("price"),
        "strike": parsed_symbol.get("strike"),
    }
    underlying_momentum_details = evaluate_option_momentum_from_snapshot(momentum_snapshot)
    option_price_momentum_details = evaluate_option_price_momentum(
        entry_option_price=entry_price,
        current_option_mid_price=current_mid_price,
        unrealized_pl_ratio=unrealized_pl_pct_ratio,
        max_pnl_pct=current_max_pnl_pct,
    )
    momentum_details = evaluate_combined_option_momentum(
        option_momentum=option_price_momentum_details,
        underlying_momentum=underlying_momentum_details,
    )
    broker_quantity = _safe_int(quantity) or 0
    exit_action, updated_state = _structured_exit_action(
        option_symbol=option_symbol,
        quantity=broker_quantity,
        unrealized_pl_pct_ratio=unrealized_pl_pct_ratio,
        days_to_expiration=days_to_expiration,
        hours_to_expiration=hours_to_expiration,
        exit_thresholds=exit_thresholds,
        position_state=position_state,
        critical_errors=critical_errors,
        context_notes=context_notes,
        enable_trailing_profit=enable_trailing_profit,
        trailing_profit_config=trailing_profit_config,
        momentum_history_config=momentum_history_config,
        momentum_status=str(momentum_details.get("status") or ""),
        momentum_reasons=list(momentum_details.get("reasons") or []),
        option_momentum_status=str(momentum_details.get("option_status") or ""),
        underlying_momentum_status=str(momentum_details.get("underlying_status") or ""),
        momentum_negative_score=safe_float(momentum_details.get("negative_score")),
        recently_filled_order=(
            _normalize_order_status((pending_reconciliation or {}).get("pending_order_status")) == "filled"
        ),
    )
    updated_state.setdefault("entry_underlying_symbol", parsed_symbol.get("underlying_symbol") or "")
    updated_state.setdefault("entry_price", entry_price)
    updated_state.setdefault("entry_qty", broker_quantity)
    updated_state.setdefault("entry_underlying_price", stock_quote.get("price"))
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
        "days_to_expiration": days_to_expiration,
        "hours_to_expiration": hours_to_expiration,
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
        "qty_to_sell": exit_action.get("qty_to_sell"),
        "sell_fraction": exit_action.get("sell_fraction"),
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
        "momentum_tracking_active": bool(persisted_state.get("momentum_tracking_active")),
        "momentum_history_sample_count": int(persisted_state.get("momentum_history_sample_count") or 0),
        "momentum_bad_count": int(persisted_state.get("momentum_bad_count") or 0),
        "momentum_mixed_count": int(persisted_state.get("momentum_mixed_count") or 0),
        "momentum_negative_score": safe_float(persisted_state.get("momentum_negative_score")) or 0.0,
        "momentum_consecutive_bad_count": int(persisted_state.get("momentum_consecutive_bad_count") or 0),
        "trailing_profit_enabled": enable_trailing_profit,
        "trailing_profit_dry_run": trailing_profit_dry_run,
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
