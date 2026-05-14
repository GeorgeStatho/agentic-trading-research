from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime
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
from services.option_dte_buckets import OPTION_DTE_BUCKETS, resolve_dte_bucket_for_days
from .utils import mid_price, safe_float


@dataclass(frozen=True)
class OptionExitRule:
    label: str
    min_days_to_expiration: int
    max_days_to_expiration: int
    take_profit_pct: float
    stop_loss_pct: float
    force_exit_days_to_expiration: int | None


OPTION_SYMBOL_TEMPLATE = r"\d{6}[CP]\d{8}$"
OPTION_SYMBOL_PATTERN = re.compile(
    r"^(?P<underlying>[A-Z0-9]{1,8})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<contract_type>[CP])(?P<strike>\d{8})$"
)

_OPTION_POSITION_SETTINGS = OptionPositionSettings.from_env()
DEFAULT_OPTION_TAKE_PROFIT_PCT = _OPTION_POSITION_SETTINGS.take_profit_pct
DEFAULT_OPTION_STOP_LOSS_PCT = _OPTION_POSITION_SETTINGS.stop_loss_pct
DEFAULT_OPTION_EXIT_HOURS_TO_EXPIRATION = _OPTION_POSITION_SETTINGS.exit_hours_to_expiration

_OPTION_EXIT_RULE_SETTINGS_BY_KEY = {
    rule.key: rule for rule in _OPTION_POSITION_SETTINGS.dte_rules
}

OPTION_EXIT_DTE_RULES = tuple(
    OptionExitRule(
        label=bucket.label,
        min_days_to_expiration=bucket.min_days,
        max_days_to_expiration=bucket.max_days,
        take_profit_pct=_OPTION_EXIT_RULE_SETTINGS_BY_KEY[bucket.key].take_profit_pct,
        stop_loss_pct=_OPTION_EXIT_RULE_SETTINGS_BY_KEY[bucket.key].stop_loss_pct,
        force_exit_days_to_expiration=_OPTION_EXIT_RULE_SETTINGS_BY_KEY[
            bucket.key
        ].force_exit_days_to_expiration,
    )
    for bucket in OPTION_DTE_BUCKETS
    if bucket.key in _OPTION_EXIT_RULE_SETTINGS_BY_KEY
)

MARKET_TIMEZONE = ZoneInfo("America/New_York")
OPTION_EXPIRATION_MARKET_CLOSE_HOUR = 16


@dataclass(frozen=True)
class ExitThresholds:
    dte_rule_label: str
    take_profit_pct: float
    stop_loss_pct: float
    force_exit_days_to_expiration: int | None
    exit_hours_to_expiration: float
    is_default_rule: bool


def _parse_option_symbol(symbol: str) -> dict[str, Any]:
    # Decode the OCC-style option symbol once so the exit logic can work with
    # explicit underlying, expiration, contract type, and strike fields.
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
    # Fetch the latest option quote and normalize it into one stable shape so
    # downstream exit checks do not have to know Alpaca's response details.
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
    # Pull the underlying stock quote alongside the option quote so position
    # snapshots can include a reference price even when only bid/ask is available.
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


def _resolve_option_exit_thresholds(
    *,
    days_to_expiration: int | None,
    default_take_profit_pct: float,
    default_stop_loss_pct: float,
    default_exit_hours_to_expiration: float,
) -> ExitThresholds:
    # Prefer the configured DTE buckets when the contract expiration is known,
    # then fall back to the global defaults for incomplete or long-dated symbols.
    resolved_bucket = resolve_dte_bucket_for_days(
        days_to_expiration,
        prefer_higher_boundary=True,
    )
    if resolved_bucket is not None:
        rule = next(
            (candidate for candidate in OPTION_EXIT_DTE_RULES if candidate.label == resolved_bucket.label),
            None,
        )
        if rule is not None:
            force_exit_days_to_expiration = rule.force_exit_days_to_expiration
            return ExitThresholds(
                dte_rule_label=rule.label,
                take_profit_pct=rule.take_profit_pct,
                stop_loss_pct=rule.stop_loss_pct,
                force_exit_days_to_expiration=force_exit_days_to_expiration,
                exit_hours_to_expiration=(
                    float(force_exit_days_to_expiration * 24)
                    if force_exit_days_to_expiration is not None
                    else default_exit_hours_to_expiration
                ),
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
    # Evaluate the pure rules first so the caller gets both a simple hold/sell
    # decision and human-readable reasons that explain exactly which threshold fired.
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


def _build_option_position_snapshot(
    position: Any,
    *,
    take_profit_pct: float,
    stop_loss_pct: float,
    exit_hours_to_expiration: float,
) -> dict[str, Any]:
    # Combine quote lookups, derived P/L and expiration math, and deterministic
    # exit rules into one snapshot that the manager can log, test, and act on.
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
    exit_thresholds = _resolve_option_exit_thresholds(
        days_to_expiration=days_to_expiration,
        default_take_profit_pct=take_profit_pct,
        default_stop_loss_pct=stop_loss_pct,
        default_exit_hours_to_expiration=exit_hours_to_expiration,
    )
    decision, decision_reasons = _deterministic_option_exit_decision(
        unrealized_pl_pct=unrealized_pl_pct,
        hours_to_expiration=hours_to_expiration,
        take_profit_pct=exit_thresholds.take_profit_pct,
        stop_loss_pct=exit_thresholds.stop_loss_pct,
        exit_hours_to_expiration=exit_thresholds.exit_hours_to_expiration,
    )

    quote_errors = [error for error in (option_quote.get("error"), stock_quote.get("error")) if error]

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
        "days_to_expiration": days_to_expiration,
        "hours_to_expiration": hours_to_expiration,
        "dte_exit_rule": exit_thresholds.dte_rule_label,
        "take_profit_pct": exit_thresholds.take_profit_pct,
        "stop_loss_pct": exit_thresholds.stop_loss_pct,
        "force_exit_days_to_expiration": exit_thresholds.force_exit_days_to_expiration,
        "exit_hours_to_expiration": exit_thresholds.exit_hours_to_expiration,
        "uses_default_exit_thresholds": exit_thresholds.is_default_rule,
        "underlying_stock_price": stock_quote.get("price"),
        "decision": decision,
        "decision_reasons": decision_reasons,
        "option_quote_timestamp": option_quote.get("timestamp"),
        "underlying_quote_timestamp": stock_quote.get("timestamp"),
        "quote_errors": quote_errors,
        "close_submitted": False,
        "close_order_id": "",
        "close_order_status": "",
        "close_error": "",
    }


def CloseOptionPositions(
    company: str | None = None,
    *,
    trading_client_override: TradingClient | None = None,
) -> list[dict[str, Any]]:
    # Close every matching option position immediately and return a compact
    # per-symbol summary so callers can persist what was submitted to the broker.
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
    trading_client_override: TradingClient | None = None,
) -> dict[str, Any]:
    # Walk every open option position, build a consistent decision snapshot, and
    # optionally submit closes so monitoring and automation share the same payload.
    position_summaries: list[dict[str, Any]] = []
    active_trading_client = trading_client_override or get_default_trading_client()
    positions = active_trading_client.get_all_positions()

    for position in positions or []:
        symbol = str(getattr(position, "symbol", "") or "").strip().upper()
        if not _is_option_position_symbol(symbol, company):
            continue

        summary = _build_option_position_snapshot(
            position,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            exit_hours_to_expiration=exit_hours_to_expiration,
        )

        if execute_sales and summary["decision"] == "sell":
            try:
                close_order = active_trading_client.close_position(symbol)
                summary["close_submitted"] = True
                summary["close_order_id"] = str(getattr(close_order, "id", ""))
                summary["close_order_status"] = str(getattr(close_order, "status", ""))
            except Exception as exc:
                summary["close_error"] = str(exc)

        position_summaries.append(summary)

    return {
        "evaluated_at": datetime.now().isoformat(),
        "company_filter": str(company or "").strip().upper(),
        "execute_sales": execute_sales,
        "uses_dte_based_exit_thresholds": True,
        "take_profit_pct": take_profit_pct,
        "stop_loss_pct": stop_loss_pct,
        "exit_hours_to_expiration": exit_hours_to_expiration,
        "position_count": len(position_summaries),
        "sell_count": sum(1 for summary in position_summaries if summary.get("decision") == "sell"),
        "close_submitted_count": sum(1 for summary in position_summaries if summary.get("close_submitted")),
        "positions": position_summaries,
    }
