"""Production's option exit rules walked over backtest marks, so a backtest exits where the manager would."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from services.core.trading_config import OptionPositionSettings

from ._option_positions_defaults import ExitThresholds, build_option_exit_rules
from ._option_positions_market import _compute_atr_pct, _resolve_option_exit_thresholds, _resolve_volatility_scale
from ._option_positions_strategy import _structured_exit_action

SELLS = {"sell_full", "sell_partial"}
SCALE_OUT_FLAGS = {"first_scale_out_trigger": "took_first_scale_out", "second_scale_out_trigger": "took_second_scale_out"}


def daily_atr_pct(bars: list[dict[str, Any]], settings: OptionPositionSettings) -> float | None:
    """The ATR production scales its thresholds by, from daily {high, low, close} bars before entry."""
    return _compute_atr_pct(bars, period=settings.volatility_atr_period)


def rule_thresholds(entry_dte: int | None, settings: OptionPositionSettings, *, take_profit_pct: float | None = None,
                    stop_loss_pct: float | None = None) -> ExitThresholds:
    """Production's exit rule for a contract's DTE at entry; a given level replaces the rule's, to test others."""
    thresholds = _resolve_option_exit_thresholds(
        days_to_expiration=entry_dte,
        default_take_profit_pct=settings.take_profit_pct,
        default_stop_loss_pct=settings.stop_loss_pct,
        default_exit_hours_to_expiration=settings.exit_hours_to_expiration,
        exit_rules=build_option_exit_rules(settings.dte_rules),
    )
    return replace(thresholds,
                   take_profit_pct=thresholds.take_profit_pct if take_profit_pct is None else take_profit_pct,
                   stop_loss_pct=thresholds.stop_loss_pct if stop_loss_pct is None else stop_loss_pct)


def production_exit(
    marks: list[tuple[datetime, float, float, float | None]],
    *,
    entry_price: float,
    quantity: int,
    side: str,
    entry_spot: float | None,
    expiration: datetime,
    entry_dte: int,
    atr_pct: float | None = None,
    settings: OptionPositionSettings | None = None,
    take_profit_pct: float | None = None,
    stop_loss_pct: float | None = None,
) -> tuple[list[tuple[datetime, int, float]], list[str]]:
    """Walk (at, mid, exit_bid, spot) marks; return the fills (at, qty, exit_bid) and each fill's exit reason.

    A given take-profit or stop-loss replaces the DTE rule's (see `rule_thresholds`) and is volatility-scaled
    like it. Momentum and V2 position exits never fire: a backtest has no momentum feed or V2 exit context.
    """
    settings = settings or OptionPositionSettings.from_env()
    thresholds = rule_thresholds(entry_dte, settings, take_profit_pct=take_profit_pct, stop_loss_pct=stop_loss_pct)
    scale = 1.0
    if settings.volatility_scaled_thresholds:
        scale = _resolve_volatility_scale(current_atr_pct=atr_pct, baseline_atr_pct=settings.volatility_baseline_atr_pct,
                                          min_scale=settings.volatility_min_scale, max_scale=settings.volatility_max_scale)
    thresholds = replace(thresholds, stop_loss_pct=round(thresholds.stop_loss_pct * scale, 4),
                         take_profit_pct=round(thresholds.take_profit_pct * scale, 4))
    direction = 1.0 if side == "call" else -1.0
    remaining = quantity
    state: dict[str, Any] = {}
    fills: list[tuple[datetime, int, float]] = []
    reasons: list[str] = []
    for at, mid, exit_bid, spot in marks:
        pnl = (mid - entry_price) / entry_price
        adverse = -direction * (spot / entry_spot - 1) if spot and entry_spot else None
        action, state = _structured_exit_action(
            option_symbol="", quantity=remaining, unrealized_pl_pct_ratio=pnl, broker_unrealized_pl_pct_ratio=pnl,
            exit_rule_days_to_expiration=entry_dte,
            hours_to_expiration=(expiration - at).total_seconds() / 3600,
            exit_thresholds=thresholds, position_state=state, critical_errors=[], context_notes=[],
            enable_trailing_profit=settings.enable_trailing_profit, trailing_profit_config=settings.trailing,
            momentum_history_config=settings.momentum_history, momentum_status="unknown", momentum_reasons=[],
            recently_filled_order=False,
            # shortcut: production confirms a stop over 3 polls 25s apart (~75s), inside one hourly mark;
            # pass the real count if marks get finer than a minute.
            stop_loss_confirm_checks=1,
            underlying_adverse_move_pct=adverse,
            stop_loss_underlying_move_pct=round(settings.stop_loss_underlying_move_pct * scale, 6),
        )
        if action["action"] not in SELLS:
            continue
        sold = remaining if action["action"] == "sell_full" else min(remaining, int(action["qty_to_sell"]))
        fills.append((at, sold, exit_bid))
        reasons.append(action["reason"])
        remaining -= sold
        if action["reason"] in SCALE_OUT_FLAGS:
            state[SCALE_OUT_FLAGS[action["reason"]]] = True
        if remaining <= 0:
            break
    return fills, reasons
