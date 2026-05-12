from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from alpaca.data.requests import OptionLatestQuoteRequest, StockLatestQuoteRequest
from alpaca.trading.client import TradingClient

from .clients import (
    get_default_trading_client,
    get_option_history_client,
    get_stock_history_client,
)
from services.config import OptionPositionSettings
from services.common import env_float
from .utils import mid_price, safe_float
from services.option_momentum import MOMENTUM_BAD, evaluate_option_momentum_from_snapshot
from services.option_position_state import (
    delete_position_state,
    get_position_state,
    update_position_state,
)


@dataclass(frozen=True)
class OptionExitRule:
    label: str
    min_days_to_expiration: int
    max_days_to_expiration: int
    take_profit_pct: float
    stop_loss_pct: float
    force_exit_days_to_expiration: int


OPTION_SYMBOL_TEMPLATE = r"\d{6}[CP]\d{8}$"
OPTION_SYMBOL_PATTERN = re.compile(
    r"^(?P<underlying>[A-Z0-9]{1,8})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<contract_type>[CP])(?P<strike>\d{8})$"
)

_OPTION_POSITION_SETTINGS = OptionPositionSettings.from_env()
DEFAULT_OPTION_TAKE_PROFIT_PCT = _OPTION_POSITION_SETTINGS.take_profit_pct
DEFAULT_OPTION_STOP_LOSS_PCT = _OPTION_POSITION_SETTINGS.stop_loss_pct
DEFAULT_OPTION_EXIT_HOURS_TO_EXPIRATION = _OPTION_POSITION_SETTINGS.exit_hours_to_expiration
DEFAULT_OPTION_POSITION_STATE_PATH = Path(
    os.getenv("OPTION_POSITION_STATE_PATH", "/shared/option_position_state.json")
)
DEFAULT_OPTION_POSITION_ENABLE_TRAILING_PROFIT = str(
    os.getenv("OPTION_POSITION_ENABLE_TRAILING_PROFIT", "false")
).strip().lower() not in {"0", "false", "no", "off"}
DEFAULT_OPTION_POSITION_TRAILING_PROFIT_DRY_RUN = str(
    os.getenv("OPTION_POSITION_TRAILING_PROFIT_DRY_RUN", "false")
).strip().lower() not in {"0", "false", "no", "off"}
DEFAULT_OPTION_POSITION_ENABLE_MOMENTUM_EXIT = str(
    os.getenv("OPTION_POSITION_ENABLE_MOMENTUM_EXIT", "false")
).strip().lower() not in {"0", "false", "no", "off"}
DEFAULT_OPTION_TRAIL_PROTECTION_TRIGGER_PCT = env_float("OPTION_TRAIL_PROTECTION_TRIGGER_PCT", 0.40)
DEFAULT_OPTION_TRAIL_INITIAL_FLOOR_PCT = env_float("OPTION_TRAIL_INITIAL_FLOOR_PCT", 0.10)
DEFAULT_OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT = env_float(
    "OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT",
    0.55,
)
DEFAULT_OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION = env_float(
    "OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION",
    0.50,
)
DEFAULT_OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT = env_float(
    "OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT",
    2.00,
)
DEFAULT_OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION = env_float(
    "OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION",
    0.25,
)
DEFAULT_OPTION_TRAIL_3_7_GIVEBACK_PCT = env_float("OPTION_TRAIL_3_7_GIVEBACK_PCT", 0.25)
DEFAULT_OPTION_TRAIL_7_14_GIVEBACK_PCT = env_float("OPTION_TRAIL_7_14_GIVEBACK_PCT", 0.35)
DEFAULT_OPTION_TRAIL_14_30_GIVEBACK_PCT = env_float("OPTION_TRAIL_14_30_GIVEBACK_PCT", 0.45)
DEFAULT_OPTION_TRAIL_100_FLOOR_PCT = env_float("OPTION_TRAIL_100_FLOOR_PCT", 0.60)
DEFAULT_OPTION_TRAIL_150_FLOOR_PCT = env_float("OPTION_TRAIL_150_FLOOR_PCT", 1.00)
DEFAULT_OPTION_TRAIL_200_FLOOR_PCT = env_float("OPTION_TRAIL_200_FLOOR_PCT", 1.40)
DEFAULT_OPTION_PENDING_EXIT_STALE_MINUTES = env_float("OPTION_PENDING_EXIT_STALE_MINUTES", 10.0)
DEFAULT_OPTION_PENDING_EXIT_CANCEL_ON_STALE = str(
    os.getenv("OPTION_PENDING_EXIT_CANCEL_ON_STALE", "true")
).strip().lower() not in {"0", "false", "no", "off"}

OPTION_EXIT_DTE_RULES = (
    OptionExitRule(
        label="3-7 DTE",
        min_days_to_expiration=3,
        max_days_to_expiration=7,
        take_profit_pct=_OPTION_POSITION_SETTINGS.dte_rules[0].take_profit_pct,
        stop_loss_pct=_OPTION_POSITION_SETTINGS.dte_rules[0].stop_loss_pct,
        force_exit_days_to_expiration=_OPTION_POSITION_SETTINGS.dte_rules[0].force_exit_days_to_expiration,
    ),
    OptionExitRule(
        label="7-14 DTE",
        min_days_to_expiration=7,
        max_days_to_expiration=14,
        take_profit_pct=_OPTION_POSITION_SETTINGS.dte_rules[1].take_profit_pct,
        stop_loss_pct=_OPTION_POSITION_SETTINGS.dte_rules[1].stop_loss_pct,
        force_exit_days_to_expiration=_OPTION_POSITION_SETTINGS.dte_rules[1].force_exit_days_to_expiration,
    ),
    OptionExitRule(
        label="14-30 DTE",
        min_days_to_expiration=14,
        max_days_to_expiration=30,
        take_profit_pct=_OPTION_POSITION_SETTINGS.dte_rules[2].take_profit_pct,
        stop_loss_pct=_OPTION_POSITION_SETTINGS.dte_rules[2].stop_loss_pct,
        force_exit_days_to_expiration=_OPTION_POSITION_SETTINGS.dte_rules[2].force_exit_days_to_expiration,
    ),
)

MARKET_TIMEZONE = ZoneInfo("America/New_York")
OPTION_EXPIRATION_MARKET_CLOSE_HOUR = 16
PENDING_ORDER_OPEN_STATUSES = {
    "accepted",
    "accepted_for_bidding",
    "calculated",
    "held",
    "new",
    "partially_filled",
    "pending_cancel",
    "pending_new",
    "pending_replace",
    "stopped",
}
PENDING_ORDER_CLEAR_STATUSES = {
    "canceled",
    "cancelled",
    "expired",
    "filled",
    "rejected",
}


@dataclass(frozen=True)
class ExitThresholds:
    dte_rule_label: str
    take_profit_pct: float
    stop_loss_pct: float
    force_exit_days_to_expiration: int | None
    exit_hours_to_expiration: float
    is_default_rule: bool


@dataclass(frozen=True)
class TrailingProfitConfig:
    protection_trigger_pct: float
    initial_floor_pct: float
    first_scale_out_trigger_pct: float
    first_scale_out_fraction: float
    second_scale_out_trigger_pct: float
    second_scale_out_fraction: float
    giveback_3_7_pct: float
    giveback_7_14_pct: float
    giveback_14_30_pct: float
    floor_100_pct: float
    floor_150_pct: float
    floor_200_pct: float
    enable_momentum_exit: bool


@dataclass(frozen=True)
class PendingExitConfig:
    stale_minutes: float
    cancel_on_stale: bool


def _resolve_option_position_state_path() -> Path:
    return Path(os.getenv("OPTION_POSITION_STATE_PATH", str(DEFAULT_OPTION_POSITION_STATE_PATH)))


def _resolve_pending_order_id(symbol: str, order_summary: dict[str, Any]) -> str:
    order_id = str(order_summary.get("id") or "").strip()
    if order_id:
        return order_id
    submitted_at = str(order_summary.get("submitted_at") or datetime.now().isoformat()).strip()
    return f"pending:{symbol}:{submitted_at}"


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_iso_datetime(value: Any) -> datetime | None:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None

    candidate = raw_value[:-1] + "+00:00" if raw_value.endswith("Z") else raw_value
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _resolve_pending_order_age_seconds(
    existing_state: dict[str, Any],
    order_summary: dict[str, Any],
    *,
    checked_at: str,
) -> float | None:
    submitted_at = (
        order_summary.get("submitted_at")
        or existing_state.get("pending_order_submitted_at")
        or existing_state.get("last_order_submitted_at")
    )
    submitted_dt = _parse_iso_datetime(submitted_at)
    checked_dt = _parse_iso_datetime(checked_at)
    if submitted_dt is None or checked_dt is None:
        return None

    if submitted_dt.tzinfo is not None and checked_dt.tzinfo is None:
        checked_dt = checked_dt.replace(tzinfo=submitted_dt.tzinfo)
    elif submitted_dt.tzinfo is None and checked_dt.tzinfo is not None:
        submitted_dt = submitted_dt.replace(tzinfo=checked_dt.tzinfo)

    return round(max(0.0, (checked_dt - submitted_dt).total_seconds()), 3)


def _resolve_pending_fill_metrics(
    existing_state: dict[str, Any],
    order_summary: dict[str, Any],
) -> tuple[int, int, int]:
    requested_qty = max(
        0,
        _safe_int(order_summary.get("requested_qty"))
        or _safe_int(existing_state.get("pending_qty"))
        or _safe_int(order_summary.get("qty"))
        or 0,
    )
    filled_qty = max(0, _safe_int(order_summary.get("filled_qty")) or 0)
    if requested_qty > 0:
        filled_qty = min(filled_qty, requested_qty)
    remaining_qty = max(0, requested_qty - filled_qty) if requested_qty > 0 else 0
    return requested_qty, filled_qty, remaining_qty


def _parse_option_symbol(symbol: str) -> dict[str, Any]:
    normalized_symbol = str(symbol or "").strip().upper()
    match = OPTION_SYMBOL_PATTERN.match(normalized_symbol)
    if match is None:
        return {
            "symbol": normalized_symbol,
            "underlying_symbol": "",
            "expiration_date": None,
            "contract_type": "",
            "strike": None,
        }

    expiration_date = date(
        year=2000 + int(match.group("yy")),
        month=int(match.group("mm")),
        day=int(match.group("dd")),
    )
    contract_type = "call" if match.group("contract_type") == "C" else "put"

    return {
        "symbol": normalized_symbol,
        "underlying_symbol": match.group("underlying"),
        "expiration_date": expiration_date.isoformat(),
        "contract_type": contract_type,
        "strike": int(match.group("strike")) / 1000.0,
    }


def _get_latest_option_quote(symbol: str) -> dict[str, Any]:
    normalized_symbol = str(symbol or "").strip().upper()
    fallback = {
        "symbol": normalized_symbol,
        "bid_price": None,
        "ask_price": None,
        "mid_price": None,
        "timestamp": "",
        "error": "",
    }
    if not normalized_symbol:
        fallback["error"] = "Option symbol was missing."
        return fallback

    try:
        quotes = get_option_history_client().get_option_latest_quote(
            OptionLatestQuoteRequest(symbol_or_symbols=normalized_symbol)
        )
        quote = quotes.get(normalized_symbol) if hasattr(quotes, "get") else None
        if quote is None:
            fallback["error"] = f"No option quote was returned for {normalized_symbol}."
            return fallback

        bid_price = safe_float(getattr(quote, "bid_price", None))
        ask_price = safe_float(getattr(quote, "ask_price", None))

        return {
            "symbol": normalized_symbol,
            "bid_price": bid_price,
            "ask_price": ask_price,
            "mid_price": mid_price(bid_price, ask_price),
            "timestamp": str(
                getattr(quote, "timestamp", None)
                or getattr(quote, "ask_timestamp", None)
                or getattr(quote, "bid_timestamp", None)
                or ""
            ),
            "error": "",
        }
    except Exception as exc:
        fallback["error"] = str(exc)
        return fallback


def _get_latest_stock_price(symbol: str) -> dict[str, Any]:
    normalized_symbol = str(symbol or "").strip().upper()
    fallback = {
        "symbol": normalized_symbol,
        "bid_price": None,
        "ask_price": None,
        "mid_price": None,
        "price": None,
        "timestamp": "",
        "error": "",
    }
    if not normalized_symbol:
        fallback["error"] = "Underlying stock symbol was missing."
        return fallback

    try:
        quotes = get_stock_history_client().get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=normalized_symbol)
        )
        quote = quotes.get(normalized_symbol) if hasattr(quotes, "get") else None
        if quote is None:
            fallback["error"] = f"No stock quote was returned for {normalized_symbol}."
            return fallback

        bid_price = safe_float(getattr(quote, "bid_price", None))
        ask_price = safe_float(getattr(quote, "ask_price", None))
        latest_mid_price = mid_price(bid_price, ask_price)

        return {
            "symbol": normalized_symbol,
            "bid_price": bid_price,
            "ask_price": ask_price,
            "mid_price": latest_mid_price,
            "price": ask_price if ask_price is not None else bid_price if bid_price is not None else latest_mid_price,
            "timestamp": str(
                getattr(quote, "timestamp", None)
                or getattr(quote, "ask_timestamp", None)
                or getattr(quote, "bid_timestamp", None)
                or ""
            ),
            "error": "",
        }
    except Exception as exc:
        fallback["error"] = str(exc)
        return fallback


def _normalize_unrealized_pl_pct(
    position: Any,
    entry_price: float | None,
    current_mid_price: float | None,
) -> float | None:
    unrealized_plpc = safe_float(getattr(position, "unrealized_plpc", None))
    if unrealized_plpc is not None:
        return round(unrealized_plpc * 100.0, 4)

    if entry_price is None or entry_price <= 0 or current_mid_price is None:
        return None

    return round(((current_mid_price - entry_price) / entry_price) * 100.0, 4)


def _days_to_expiration(expiration_date_text: str | None) -> int | None:
    if not expiration_date_text:
        return None

    try:
        expiration_date = datetime.strptime(expiration_date_text, "%Y-%m-%d").date()
    except ValueError:
        return None

    return (expiration_date - datetime.now(MARKET_TIMEZONE).date()).days


def _hours_to_expiration(expiration_date_text: str | None) -> float | None:
    if not expiration_date_text:
        return None

    try:
        expiration_date = datetime.strptime(expiration_date_text, "%Y-%m-%d").date()
    except ValueError:
        return None

    expiration_close = datetime(
        year=expiration_date.year,
        month=expiration_date.month,
        day=expiration_date.day,
        hour=OPTION_EXPIRATION_MARKET_CLOSE_HOUR,
        tzinfo=MARKET_TIMEZONE,
    )
    now = datetime.now(MARKET_TIMEZONE)
    return round((expiration_close - now).total_seconds() / 3600.0, 4)


def _normalize_pct_ratio(percent_value: float | None) -> float | None:
    if percent_value is None:
        return None
    return round(percent_value / 100.0, 6)


def _resolve_dte_exit_rule(days_to_expiration: int | None) -> OptionExitRule | None:
    if days_to_expiration is None:
        return None

    # Match higher DTE buckets first so 7 DTE resolves to 7-14 and 14 DTE resolves to 14-30.
    for rule in reversed(OPTION_EXIT_DTE_RULES):
        if rule.min_days_to_expiration <= days_to_expiration <= rule.max_days_to_expiration:
            return rule

    return None


def _resolve_option_exit_thresholds(
    *,
    days_to_expiration: int | None,
    default_take_profit_pct: float,
    default_stop_loss_pct: float,
    default_exit_hours_to_expiration: float,
) -> ExitThresholds:
    rule = _resolve_dte_exit_rule(days_to_expiration)
    if rule is not None:
        return ExitThresholds(
            dte_rule_label=rule.label,
            take_profit_pct=rule.take_profit_pct,
            stop_loss_pct=rule.stop_loss_pct,
            force_exit_days_to_expiration=rule.force_exit_days_to_expiration,
            exit_hours_to_expiration=float(rule.force_exit_days_to_expiration * 24),
            is_default_rule=False,
        )

    return ExitThresholds(
        dte_rule_label="default",
        take_profit_pct=default_take_profit_pct,
        stop_loss_pct=default_stop_loss_pct,
        force_exit_days_to_expiration=None,
        exit_hours_to_expiration=default_exit_hours_to_expiration,
        is_default_rule=True,
    )


def _is_option_position_symbol(symbol: str, company: str | None = None) -> bool:
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        return False

    if company:
        normalized_company = str(company).strip().upper()
        pattern = rf"^{re.escape(normalized_company)}{OPTION_SYMBOL_TEMPLATE}"
        return bool(re.match(pattern, normalized_symbol))

    return bool(re.search(OPTION_SYMBOL_TEMPLATE, normalized_symbol))


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
        reasons.append(
            f"Take-profit triggered at {unrealized_pl_pct:.2f}% versus target {take_profit_pct:.2f}%."
        )
    if unrealized_pl_pct is not None and unrealized_pl_pct <= stop_loss_pct:
        reasons.append(
            f"Stop-loss triggered at {unrealized_pl_pct:.2f}% versus floor {stop_loss_pct:.2f}%."
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


def _resolve_trailing_giveback_pct(
    days_to_expiration: int | None,
    trailing_profit_config: TrailingProfitConfig,
) -> float:
    rule = _resolve_dte_exit_rule(days_to_expiration)
    if rule is None:
        return trailing_profit_config.giveback_7_14_pct
    if rule.label == "3-7 DTE":
        return trailing_profit_config.giveback_3_7_pct
    if rule.label == "7-14 DTE":
        return trailing_profit_config.giveback_7_14_pct
    if rule.label == "14-30 DTE":
        return trailing_profit_config.giveback_14_30_pct
    return trailing_profit_config.giveback_14_30_pct


def _compute_scaled_sell_qty(quantity: int, sell_fraction: float) -> int:
    if quantity <= 0 or sell_fraction <= 0:
        return 0
    requested_qty = int(round(quantity * sell_fraction))
    return max(1, min(quantity, requested_qty))


def _normalize_order_status(status: Any) -> str:
    return str(status or "").strip().lower()


def _reconcile_pending_order_state(
    *,
    state_path: Path,
    option_symbol: str,
    position_state: dict[str, Any] | None,
    resolve_order_status: Any | None,
    cancel_order: Any | None,
    broker_quantity: int | None,
    pending_exit_config: PendingExitConfig,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    existing_state = dict(position_state or {})
    reconciliation = {
        "pending_order_status": "",
        "pending_order_last_checked_at": "",
        "pending_order_terminal": False,
        "pending_order_error": "",
        "pending_order_age_seconds": None,
        "pending_order_stale": False,
        "pending_order_cancel_submitted": False,
        "pending_order_filled_qty": 0,
        "pending_order_remaining_qty": 0,
    }
    pending_order_id = str(existing_state.get("pending_order_id") or "").strip()
    if not pending_order_id or resolve_order_status is None:
        return position_state, reconciliation

    checked_at = datetime.now().isoformat()
    try:
        order_summary = resolve_order_status(pending_order_id)
    except Exception as exc:
        reconciliation["pending_order_error"] = str(exc)
        reconciliation["pending_order_last_checked_at"] = checked_at
        return position_state, reconciliation

    normalized_status = _normalize_order_status(order_summary.get("status"))
    _, filled_qty, remaining_qty = _resolve_pending_fill_metrics(existing_state, order_summary)
    order_age_seconds = _resolve_pending_order_age_seconds(
        existing_state,
        order_summary,
        checked_at=checked_at,
    )
    pending_order_stale = (
        pending_exit_config.cancel_on_stale
        and order_age_seconds is not None
        and order_age_seconds >= max(0.0, pending_exit_config.stale_minutes) * 60.0
    )
    reconciliation["pending_order_status"] = normalized_status
    reconciliation["pending_order_last_checked_at"] = checked_at
    reconciliation["pending_order_age_seconds"] = order_age_seconds
    reconciliation["pending_order_stale"] = pending_order_stale
    reconciliation["pending_order_filled_qty"] = filled_qty
    reconciliation["pending_order_remaining_qty"] = remaining_qty
    updates: dict[str, Any] = {
        "pending_order_last_checked_at": checked_at,
        "last_reconciled_order_status": normalized_status,
        "pending_order_submitted_at": str(
            order_summary.get("submitted_at")
            or existing_state.get("pending_order_submitted_at")
            or existing_state.get("last_order_submitted_at")
            or ""
        ),
        "pending_filled_qty": filled_qty,
        "pending_remaining_qty": remaining_qty,
    }
    if broker_quantity is not None:
        updates["last_seen_qty"] = int(broker_quantity)
        updates["remaining_qty"] = int(broker_quantity)
    if filled_qty > 0:
        fill_timestamp = str(order_summary.get("filled_at") or order_summary.get("updated_at") or checked_at)
        updates["last_partial_fill_at"] = fill_timestamp
        updates["last_filled_qty"] = filled_qty

    if normalized_status in PENDING_ORDER_OPEN_STATUSES and pending_order_stale:
        if cancel_order is None:
            reconciliation["pending_order_error"] = (
                "Pending exit order exceeded the stale timeout but no cancel callback was provided."
            )
            persisted_state = update_position_state(state_path, option_symbol, updates)
            return persisted_state, reconciliation
        try:
            cancel_summary = cancel_order(pending_order_id)
            reconciliation["pending_order_cancel_submitted"] = True
            updates["last_cancel_attempt_at"] = checked_at
            order_summary = {**order_summary, **(cancel_summary or {})}
            normalized_status = _normalize_order_status(order_summary.get("status"))
            _, filled_qty, remaining_qty = _resolve_pending_fill_metrics(existing_state, order_summary)
            reconciliation["pending_order_status"] = normalized_status
            reconciliation["pending_order_filled_qty"] = filled_qty
            reconciliation["pending_order_remaining_qty"] = remaining_qty
            updates["last_reconciled_order_status"] = normalized_status
            updates["pending_filled_qty"] = filled_qty
            updates["pending_remaining_qty"] = remaining_qty
            if filled_qty > 0:
                fill_timestamp = str(order_summary.get("filled_at") or order_summary.get("updated_at") or checked_at)
                updates["last_partial_fill_at"] = fill_timestamp
                updates["last_filled_qty"] = filled_qty
        except Exception as exc:
            reconciliation["pending_order_error"] = str(exc)
            persisted_state = update_position_state(state_path, option_symbol, updates)
            return persisted_state, reconciliation

    if normalized_status in PENDING_ORDER_OPEN_STATUSES:
        persisted_state = update_position_state(state_path, option_symbol, updates)
        return persisted_state, reconciliation

    if normalized_status in PENDING_ORDER_CLEAR_STATUSES:
        pending_reason = str(existing_state.get("pending_reason") or "").strip()
        pending_action = str(existing_state.get("pending_action") or "").strip()
        updates.update(
            {
                "pending_order_id": "",
                "pending_action": "",
                "pending_qty": 0,
                "pending_reason": "",
                "pending_order_submitted_at": "",
                "pending_filled_qty": 0,
                "pending_remaining_qty": 0,
            }
        )
        if normalized_status == "filled":
            updates["last_filled_at"] = str(
                order_summary.get("filled_at")
                or order_summary.get("updated_at")
                or checked_at
            )
            updates["last_filled_qty"] = filled_qty or (_safe_int(existing_state.get("pending_qty")) or 0)
        elif filled_qty > 0:
            updates["last_filled_at"] = str(
                order_summary.get("filled_at")
                or order_summary.get("updated_at")
                or checked_at
            )
        elif pending_reason == "first_scale_out_trigger":
            updates["took_first_scale_out"] = False
        elif pending_reason == "second_scale_out_trigger":
            updates["took_second_scale_out"] = False
        if normalized_status in {"canceled", "cancelled", "expired", "rejected"}:
            updates["last_retryable_exit_status"] = normalized_status
            updates["last_retryable_exit_reason"] = pending_reason
            updates["last_retryable_exit_action"] = pending_action
            updates["last_retryable_exit_at"] = checked_at

        persisted_state = update_position_state(state_path, option_symbol, updates)
        reconciliation["pending_order_terminal"] = True
        return persisted_state, reconciliation

    persisted_state = update_position_state(state_path, option_symbol, updates)
    return persisted_state, reconciliation


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
    momentum_status: str,
    momentum_reasons: list[str],
    recently_filled_order: bool,
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
                notes=notes
                + [
                    f"Stop-loss triggered at {unrealized_pl_pct_ratio * 100.0:.2f}% versus floor {exit_thresholds.stop_loss_pct:.2f}%."
                ],
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
                notes=notes
                + [
                    f"Hours to expiration is {hours_to_expiration:.2f}, at or below exit threshold {exit_thresholds.exit_hours_to_expiration:.2f}."
                ],
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
        protected_profit_floor_pct = max(
            protected_profit_floor_pct or 0.0,
            trailing_profit_config.initial_floor_pct,
        )
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

    if (
        trailing_profit_config.enable_momentum_exit
        and momentum_status == MOMENTUM_BAD
        and current_max_pnl_pct is not None
        and current_max_pnl_pct >= trailing_profit_config.first_scale_out_trigger_pct
    ):
        updated_state["last_action"] = "sell_full"
        updated_state["last_decision_reason"] = "momentum_failed_after_tp"
        return (
            _build_exit_action(
                action="sell_full",
                reason="momentum_failed_after_tp",
                pnl_pct=unrealized_pl_pct_ratio,
                max_pnl_pct=current_max_pnl_pct,
                protected_profit_floor_pct=protected_profit_floor_pct,
                trailing_giveback_pct=trailing_giveback_pct,
                sell_fraction=1.0,
                qty_to_sell=quantity,
                notes=notes + momentum_reasons,
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


def _build_option_position_snapshot(
    position: Any,
    *,
    take_profit_pct: float,
    stop_loss_pct: float,
    exit_hours_to_expiration: float,
    enable_trailing_profit: bool,
    trailing_profit_dry_run: bool,
    trailing_profit_config: TrailingProfitConfig,
    state_path: Path,
    position_state_override: dict[str, Any] | None = None,
    pending_reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    option_symbol = str(getattr(position, "symbol", "") or "").strip().upper()
    parsed_symbol = _parse_option_symbol(option_symbol)
    option_quote = _get_latest_option_quote(option_symbol)
    stock_quote = _get_latest_stock_price(parsed_symbol.get("underlying_symbol"))

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
    position_state = (
        position_state_override
        if position_state_override is not None
        else get_position_state(state_path, option_symbol)
    )
    momentum_snapshot = {
        "contract_type": parsed_symbol.get("contract_type"),
        "entry_underlying_price": (position_state or {}).get("entry_underlying_price"),
        "underlying_stock_price": stock_quote.get("price"),
        "strike": parsed_symbol.get("strike"),
    }
    momentum_details = evaluate_option_momentum_from_snapshot(momentum_snapshot)
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
        momentum_status=str(momentum_details.get("status") or ""),
        momentum_reasons=list(momentum_details.get("reasons") or []),
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


def _mark_exit_submission(
    *,
    state_path: Path,
    option_symbol: str,
    summary: dict[str, Any],
    order_summary: dict[str, Any],
) -> None:
    existing_state = get_position_state(state_path, option_symbol) or {}
    requested_qty = _safe_int(order_summary.get("requested_qty")) or _safe_int(order_summary.get("qty")) or 0
    pending_order_id = _resolve_pending_order_id(option_symbol, order_summary)
    pending_updates: dict[str, Any] = {
        "pending_order_id": pending_order_id,
        "pending_action": str(summary.get("exit_action") or ""),
        "pending_qty": requested_qty,
        "pending_reason": str(summary.get("exit_reason") or ""),
        "last_order_submitted_at": str(order_summary.get("submitted_at") or datetime.now().isoformat()),
        "pending_order_submitted_at": str(order_summary.get("submitted_at") or datetime.now().isoformat()),
        "pending_filled_qty": 0,
        "pending_remaining_qty": requested_qty,
        "last_action": str(summary.get("exit_action") or ""),
    }
    if (
        str(existing_state.get("last_retryable_exit_reason") or "").strip() != str(summary.get("exit_reason") or "").strip()
        or str(existing_state.get("last_retryable_exit_action") or "").strip() != str(summary.get("exit_action") or "").strip()
    ):
        pending_updates["last_retryable_exit_reason"] = ""
        pending_updates["last_retryable_exit_action"] = ""
        pending_updates["last_retryable_exit_status"] = ""
        pending_updates["last_retryable_exit_at"] = ""
    if summary.get("exit_reason") == "first_scale_out_trigger":
        pending_updates["took_first_scale_out"] = True
    if summary.get("exit_reason") == "second_scale_out_trigger":
        pending_updates["took_second_scale_out"] = True

    persisted_state = update_position_state(state_path, option_symbol, pending_updates)
    summary["pending_order_id"] = pending_order_id
    summary["pending_order_status"] = str(order_summary.get("status") or "")
    summary["pending_order_last_checked_at"] = str(order_summary.get("submitted_at") or "")
    summary["pending_order_terminal"] = False
    summary["pending_order_error"] = ""
    summary["pending_order_age_seconds"] = 0.0
    summary["pending_order_stale"] = False
    summary["pending_order_cancel_submitted"] = False
    summary["pending_order_filled_qty"] = 0
    summary["pending_order_remaining_qty"] = requested_qty
    summary["last_action"] = str(persisted_state.get("last_action") or summary.get("exit_action") or "")
    summary["last_action_at"] = str(persisted_state.get("updated_at") or "")
    summary["took_first_scale_out"] = bool(persisted_state.get("took_first_scale_out"))
    summary["took_second_scale_out"] = bool(persisted_state.get("took_second_scale_out"))


def CloseOptionPositions(
    company: str | None = None,
    *,
    trading_client_override: TradingClient | None = None,
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
    trail_protection_trigger_pct_override: float | None = None,
    trail_initial_floor_pct_override: float | None = None,
    trail_first_scale_out_trigger_pct_override: float | None = None,
    trail_first_scale_out_fraction_override: float | None = None,
    trail_second_scale_out_trigger_pct_override: float | None = None,
    trail_second_scale_out_fraction_override: float | None = None,
    trail_3_7_giveback_pct_override: float | None = None,
    trail_7_14_giveback_pct_override: float | None = None,
    trail_14_30_giveback_pct_override: float | None = None,
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
    trailing_profit_config = TrailingProfitConfig(
        protection_trigger_pct=float(
            DEFAULT_OPTION_TRAIL_PROTECTION_TRIGGER_PCT
            if trail_protection_trigger_pct_override is None
            else trail_protection_trigger_pct_override
        ),
        initial_floor_pct=float(
            DEFAULT_OPTION_TRAIL_INITIAL_FLOOR_PCT
            if trail_initial_floor_pct_override is None
            else trail_initial_floor_pct_override
        ),
        first_scale_out_trigger_pct=float(
            DEFAULT_OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT
            if trail_first_scale_out_trigger_pct_override is None
            else trail_first_scale_out_trigger_pct_override
        ),
        first_scale_out_fraction=float(
            DEFAULT_OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION
            if trail_first_scale_out_fraction_override is None
            else trail_first_scale_out_fraction_override
        ),
        second_scale_out_trigger_pct=float(
            DEFAULT_OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT
            if trail_second_scale_out_trigger_pct_override is None
            else trail_second_scale_out_trigger_pct_override
        ),
        second_scale_out_fraction=float(
            DEFAULT_OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION
            if trail_second_scale_out_fraction_override is None
            else trail_second_scale_out_fraction_override
        ),
        giveback_3_7_pct=float(
            DEFAULT_OPTION_TRAIL_3_7_GIVEBACK_PCT
            if trail_3_7_giveback_pct_override is None
            else trail_3_7_giveback_pct_override
        ),
        giveback_7_14_pct=float(
            DEFAULT_OPTION_TRAIL_7_14_GIVEBACK_PCT
            if trail_7_14_giveback_pct_override is None
            else trail_7_14_giveback_pct_override
        ),
        giveback_14_30_pct=float(
            DEFAULT_OPTION_TRAIL_14_30_GIVEBACK_PCT
            if trail_14_30_giveback_pct_override is None
            else trail_14_30_giveback_pct_override
        ),
        floor_100_pct=float(
            DEFAULT_OPTION_TRAIL_100_FLOOR_PCT
            if trail_100_floor_pct_override is None
            else trail_100_floor_pct_override
        ),
        floor_150_pct=float(
            DEFAULT_OPTION_TRAIL_150_FLOOR_PCT
            if trail_150_floor_pct_override is None
            else trail_150_floor_pct_override
        ),
        floor_200_pct=float(
            DEFAULT_OPTION_TRAIL_200_FLOOR_PCT
            if trail_200_floor_pct_override is None
            else trail_200_floor_pct_override
        ),
        enable_momentum_exit=(
            DEFAULT_OPTION_POSITION_ENABLE_MOMENTUM_EXIT
            if enable_momentum_exit_override is None
            else bool(enable_momentum_exit_override)
        ),
    )
    pending_exit_config = PendingExitConfig(
        stale_minutes=float(
            DEFAULT_OPTION_PENDING_EXIT_STALE_MINUTES
            if pending_exit_stale_minutes_override is None
            else pending_exit_stale_minutes_override
        ),
        cancel_on_stale=(
            DEFAULT_OPTION_PENDING_EXIT_CANCEL_ON_STALE
            if pending_exit_cancel_on_stale_override is None
            else bool(pending_exit_cancel_on_stale_override)
        ),
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

        summary = _build_option_position_snapshot(
            position,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            exit_hours_to_expiration=exit_hours_to_expiration,
            enable_trailing_profit=enable_trailing_profit,
            trailing_profit_dry_run=trailing_profit_dry_run,
            trailing_profit_config=trailing_profit_config,
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
                summary["close_error"] = (
                    "Partial sell action computed by structured engine, but no partial option sell callback was provided."
                )
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
        stale_symbols = set()
        try:
            from services.option_position_state import load_state

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
