from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Callable

from alpaca.data.requests import OptionLatestQuoteRequest, StockLatestQuoteRequest

from services.option_dte_buckets import resolve_dte_bucket_for_days

from .utils import mid_price, safe_float
from ._option_positions_defaults import (
    DEFAULT_OPTION_TRAIL_7_14_GIVEBACK_PCT,
    ExitThresholds,
    MARKET_TIMEZONE,
    OPTION_DTE_BUCKETS,
    OPTION_EXIT_DTE_RULES,
    OPTION_EXPIRATION_MARKET_CLOSE_HOUR,
    OPTION_SYMBOL_PATTERN,
    OPTION_SYMBOL_TEMPLATE,
    OptionExitRule,
    TrailingProfitConfig,
)


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


def _get_latest_option_quote(
    symbol: str,
    get_option_history_client: Callable[[], Any],
) -> dict[str, Any]:
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


def _get_latest_stock_price(
    symbol: str,
    get_stock_history_client: Callable[[], Any],
) -> dict[str, Any]:
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
    if entry_price is not None and entry_price > 0 and current_mid_price is not None:
        return round(((current_mid_price - entry_price) / entry_price) * 100.0, 4)
    unrealized_plpc = safe_float(getattr(position, "unrealized_plpc", None))
    if unrealized_plpc is not None:
        return round(unrealized_plpc * 100.0, 4)
    return None


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
    resolved_bucket = resolve_dte_bucket_for_days(days_to_expiration, prefer_higher_boundary=True)
    if resolved_bucket is not None:
        rule = next((candidate for candidate in OPTION_EXIT_DTE_RULES if candidate.label == resolved_bucket.label), None)
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


def _resolve_trailing_giveback_pct(
    days_to_expiration: int | None,
    trailing_profit_config: TrailingProfitConfig,
) -> float:
    resolved_bucket = resolve_dte_bucket_for_days(days_to_expiration, prefer_higher_boundary=True)
    fallback_bucket_key = OPTION_DTE_BUCKETS[0].key if OPTION_DTE_BUCKETS else ""
    bucket_key = resolved_bucket.key if resolved_bucket is not None else fallback_bucket_key
    resolved_value = trailing_profit_config.giveback_pct_by_bucket_key.get(bucket_key)
    if resolved_value is not None:
        return float(resolved_value)
    if trailing_profit_config.giveback_pct_by_bucket_key:
        return float(next(iter(trailing_profit_config.giveback_pct_by_bucket_key.values())))
    return DEFAULT_OPTION_TRAIL_7_14_GIVEBACK_PCT
