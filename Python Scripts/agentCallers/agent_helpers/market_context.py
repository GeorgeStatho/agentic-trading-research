from __future__ import annotations

"""Market-data services used by the manager stage.

This module owns the Alpaca-specific work needed to enrich a strategist payload
with live market context. Keeping that logic here lets ``agent_helpers.manager``
stay focused on orchestration instead of vendor details.
"""

import json
import argparse
from datetime import date, datetime
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any

if __package__ in {None, ""}:
    AGENT_CALLERS_DIR = Path(__file__).resolve().parents[1]
    if str(AGENT_CALLERS_DIR) not in sys.path:
        sys.path.append(str(AGENT_CALLERS_DIR))

from _paths import bootstrap_agent_callers


bootstrap_agent_callers(include_webscraping=True, load_env_file=True)

try:
    from alpaca.data import OptionHistoricalDataClient, StockHistoricalDataClient
    from alpaca.data.requests import OptionChainRequest, StockLatestQuoteRequest, StockLatestTradeRequest
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import ContractType
    from alpaca.trading.requests import GetOptionContractsRequest

    ALPACA_IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - optional dependency
    OptionHistoricalDataClient = None
    OptionChainRequest = None
    StockHistoricalDataClient = None
    StockLatestQuoteRequest = None
    StockLatestTradeRequest = None
    TradingClient = None
    ContractType = None
    GetOptionContractsRequest = None
    ALPACA_IMPORT_ERROR = exc

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - optional dependency
    yf = None

try:
    from yfinance_client import REQUEST_HANDLER
except ImportError:  # pragma: no cover - optional dependency
    REQUEST_HANDLER = None


DEFAULT_OPTION_CHAIN_LIMIT_PER_TYPE = max(1, int(os.getenv("MANAGER_OPTION_CHAIN_LIMIT_PER_TYPE", "6")))
DEFAULT_OPTION_CHAIN_FETCH_MULTIPLIER = max(2, int(os.getenv("MANAGER_OPTION_CHAIN_FETCH_MULTIPLIER", "6")))
DEFAULT_OPTION_FETCH_MIN = max(100, int(os.getenv("MANAGER_OPTION_FETCH_MIN", "500")))
PREFERRED_OTM_DISTANCE = 0.75
OPTION_SYMBOL_TEMPLATE = r"\d{6}[CP]\d{8}$"
_ALPACA_CLIENTS: dict[str, Any] | None | bool = None
MARKET_INDEX_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("sp500", "^GSPC", "S&P 500"),
    ("dow_jones_industrial_average", "^DJI", "Dow Jones Industrial Average"),
    ("vix", "^VIX", "CBOE Volatility Index"),
)

CLOSEST_EXPIRATION_GTE = 1
FARTHEST_EXPIRATION_LTE = 8
LOGGER = logging.getLogger("agent_helpers.market_context")


__all__ = [
    "DEFAULT_OPTION_CHAIN_LIMIT_PER_TYPE",
    "build_market_context",
]


def _env_flag(name: str, default: bool) -> bool:
    value = str(os.getenv(name, str(default))).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(source: Any, *keys: str) -> float | None:
    for key in keys:
        value = _safe_float(_get_field(source, key))
        if value is not None:
            return value
    return None


def _serialize_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()

    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float, bool)) and enum_value is not None:
        return enum_value

    iso_value = getattr(value, "isoformat", None)
    if callable(iso_value):
        try:
            return iso_value()
        except TypeError:
            pass

    return str(value)


def _get_field(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _get_alpaca_clients() -> dict[str, Any] | None:
    """Build Alpaca clients lazily so callers can use offline fallbacks."""
    global _ALPACA_CLIENTS
    if _ALPACA_CLIENTS is False:
        return None
    if isinstance(_ALPACA_CLIENTS, dict):
        return _ALPACA_CLIENTS

    if ALPACA_IMPORT_ERROR is not None:
        _ALPACA_CLIENTS = False
        return None

    api_key = str(os.getenv("PUBLIC_KEY") or "").strip()
    api_secret = str(os.getenv("PRIVATE_KEY") or "").strip()
    if not api_key or not api_secret:
        _ALPACA_CLIENTS = False
        return None

    os.environ.pop("APCA_OAUTH_TOKEN", None)
    os.environ.pop("ALPACA_OAUTH_TOKEN", None)

    paper = _env_flag("ALPACA_PAPER", True)
    _ALPACA_CLIENTS = {
        "stock": StockHistoricalDataClient(api_key, api_secret),
        "option": OptionHistoricalDataClient(api_key, api_secret),
        "trading": TradingClient(api_key=api_key, secret_key=api_secret, oauth_token=None, paper=paper),
    }
    return _ALPACA_CLIENTS


def _build_stock_fallback_snapshot(company: dict[str, Any]) -> dict[str, Any]:
    market_data = company.get("market_data", {})
    if not isinstance(market_data, dict):
        market_data = {}

    for key in ("currentPrice", "regularMarketPrice", "current_price", "price", "lastPrice", "previousClose"):
        price = _safe_float(market_data.get(key))
        if price is None:
            continue
        return {
            "available": True,
            "symbol": company.get("symbol") or "",
            "source": "company_market_data",
            "price": price,
            "bid_price": None,
            "ask_price": None,
            "midpoint_price": price,
            "timestamp": "",
        }

    return {
        "available": False,
        "symbol": company.get("symbol") or "",
        "source": "company_market_data",
        "price": None,
        "latest_trade_price": None,
        "bid_price": None,
        "ask_price": None,
        "midpoint_price": None,
        "timestamp": "",
    }


def _empty_market_index_snapshot(*, symbol: str, label: str, error: str = "") -> dict[str, Any]:
    return {
        "available": False,
        "symbol": symbol,
        "label": label,
        "source": "yfinance",
        "price": None,
        "previous_close": None,
        "absolute_change": None,
        "percent_change": None,
        "timestamp": "",
        "error": error,
    }


def _build_market_index_snapshot(*, symbol: str, label: str) -> dict[str, Any]:
    unavailable = _empty_market_index_snapshot(symbol=symbol, label=label)
    if not symbol:
        unavailable["error"] = "Market index symbol was missing."
        return unavailable

    if yf is None or REQUEST_HANDLER is None:
        unavailable["error"] = "yfinance integration is unavailable."
        return unavailable

    try:
        ticker = REQUEST_HANDLER.run(
            yf.Ticker,
            symbol,
            _context=f"Ticker({symbol})",
        )

        fast_info = REQUEST_HANDLER.run(
            lambda: ticker.fast_info,
            _context=f"fast_info({symbol})",
        )

        price = _first_float(
            fast_info,
            "lastPrice",
            "last_price",
            "regularMarketPrice",
            "regular_market_price",
            "currentPrice",
            "current_price",
        )
        previous_close = _first_float(
            fast_info,
            "previousClose",
            "previous_close",
            "regularMarketPreviousClose",
            "regular_market_previous_close",
        )
        timestamp = str(
            _get_field(fast_info, "lastTradeTime")
            or _get_field(fast_info, "last_trade_time")
            or _get_field(fast_info, "marketTime")
            or _get_field(fast_info, "market_time")
            or ""
        )

        if price is None:
            history = REQUEST_HANDLER.run(
                lambda: ticker.history(period="5d", interval="1d", auto_adjust=False, prepost=False),
                _context=f"history({symbol},5d,1d)",
            )
            if history is not None and not getattr(history, "empty", True):
                last_close = _safe_float(history["Close"].iloc[-1])
                previous_history_close = None
                if len(history.index) >= 2:
                    previous_history_close = _safe_float(history["Close"].iloc[-2])
                price = last_close
                if previous_close is None:
                    previous_close = previous_history_close
                if not timestamp:
                    timestamp = _serialize_scalar(history.index[-1]) or ""

        absolute_change = None
        percent_change = None
        if price is not None and previous_close is not None:
            absolute_change = round(price - previous_close, 4)
            if previous_close != 0:
                percent_change = round(((price - previous_close) / previous_close) * 100.0, 4)

        return {
            "available": price is not None,
            "symbol": symbol,
            "label": label,
            "source": "yfinance_fast_info" if price is not None else "yfinance",
            "price": price,
            "previous_close": previous_close,
            "absolute_change": absolute_change,
            "percent_change": percent_change,
            "timestamp": timestamp,
            "error": "" if price is not None else "No index price was returned.",
        }
    except Exception as exc:
        unavailable["error"] = str(exc)
        return unavailable


def _build_market_indices_snapshot() -> dict[str, Any]:
    return {
        key: _build_market_index_snapshot(symbol=symbol, label=label)
        for key, symbol, label in MARKET_INDEX_DEFINITIONS
    }


def _build_current_stock_price_snapshot(company: dict[str, Any]) -> dict[str, Any]:
    symbol = str(company.get("symbol") or "").strip().upper()
    fallback = _build_stock_fallback_snapshot(company)
    if not symbol:
        fallback["error"] = "Company symbol was missing."
        return fallback

    clients = _get_alpaca_clients()
    if clients is None or StockLatestQuoteRequest is None:
        fallback["error"] = (
            f"alpaca-py is unavailable: {ALPACA_IMPORT_ERROR}"
            if ALPACA_IMPORT_ERROR is not None
            else "Alpaca credentials were not configured."
        )
        return fallback

    try:
        quotes = clients["stock"].get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=symbol))
        quote = quotes.get(symbol) if hasattr(quotes, "get") else None
        if quote is None:
            fallback["error"] = f"No latest stock quote was returned for {symbol}."
            return fallback

        latest_trade_price = None
        latest_trade_timestamp = ""
        if StockLatestTradeRequest is not None:
            try:
                trades = clients["stock"].get_stock_latest_trade(
                    StockLatestTradeRequest(symbol_or_symbols=symbol)
                )
                trade = trades.get(symbol) if hasattr(trades, "get") else None
                if trade is not None:
                    latest_trade_price = _safe_float(_get_field(trade, "price"))
                    latest_trade_timestamp = (
                        _serialize_scalar(_get_field(trade, "timestamp")) or ""
                    )
            except Exception:
                latest_trade_price = None
                latest_trade_timestamp = ""

        bid_price = _safe_float(_get_field(quote, "bid_price"))
        ask_price = _safe_float(_get_field(quote, "ask_price"))
        midpoint_price = None
        if bid_price is not None and ask_price is not None:
            midpoint_price = round((bid_price + ask_price) / 2.0, 4)

        price = latest_trade_price
        if price is None:
            price = ask_price if ask_price is not None else bid_price
        if price is None:
            price = midpoint_price

        return {
            "available": price is not None,
            "symbol": symbol,
            "source": "alpaca_latest_trade" if latest_trade_price is not None else "alpaca_latest_quote",
            "price": price,
            "latest_trade_price": latest_trade_price,
            "latest_trade_timestamp": latest_trade_timestamp,
            "bid_price": bid_price,
            "ask_price": ask_price,
            "midpoint_price": midpoint_price,
            "bid_size": _serialize_scalar(_get_field(quote, "bid_size")),
            "ask_size": _serialize_scalar(_get_field(quote, "ask_size")),
            "timestamp": _serialize_scalar(_get_field(quote, "timestamp"))
            or _serialize_scalar(_get_field(quote, "ask_timestamp"))
            or _serialize_scalar(_get_field(quote, "bid_timestamp"))
            or "",
        }
    except Exception as exc:
        fallback["error"] = str(exc)
        return fallback


def _get_reference_stock_price_from_snapshot(stock_snapshot: dict[str, Any]) -> float | None:
    latest_trade = _safe_float(stock_snapshot.get("latest_trade_price"))
    bid = _safe_float(stock_snapshot.get("bid_price"))
    ask = _safe_float(stock_snapshot.get("ask_price"))
    midpoint = _safe_float(stock_snapshot.get("midpoint_price"))
    price = _safe_float(stock_snapshot.get("price"))

    if latest_trade is not None and latest_trade > 0:
        return latest_trade

    if bid is not None and ask is not None and bid > 0 and ask > 0:
        spread_ratio = (ask - bid) / max(midpoint or bid, 0.0001)
        if spread_ratio <= 0.02 and midpoint is not None and midpoint > 0:
            return midpoint

    for candidate in (price, ask, midpoint, bid):
        if candidate is not None and candidate > 0:
            return candidate

    return None


def _extract_contract_items(contracts_response: Any) -> list[Any]:
    if isinstance(contracts_response, dict):
        contract_items = contracts_response.get("option_contracts", [])
    else:
        contract_items = getattr(contracts_response, "option_contracts", contracts_response)

    if isinstance(contract_items, list):
        return contract_items

    try:
        return list(contract_items)
    except TypeError:
        return []


def _serialize_greeks(greeks: Any) -> dict[str, float | None]:
    return {
        "delta": _safe_float(_get_field(greeks, "delta")),
        "gamma": _safe_float(_get_field(greeks, "gamma")),
        "theta": _safe_float(_get_field(greeks, "theta")),
        "vega": _safe_float(_get_field(greeks, "vega")),
        "rho": _safe_float(_get_field(greeks, "rho")),
    }


def _serialize_quote_snapshot(quote: Any) -> dict[str, Any]:
    bid_price = _safe_float(_get_field(quote, "bid_price"))
    ask_price = _safe_float(_get_field(quote, "ask_price"))
    midpoint_price = None
    if bid_price is not None and ask_price is not None:
        midpoint_price = round((bid_price + ask_price) / 2.0, 4)

    return {
        "bid_price": bid_price,
        "ask_price": ask_price,
        "midpoint_price": midpoint_price,
        "bid_size": _serialize_scalar(_get_field(quote, "bid_size")),
        "ask_size": _serialize_scalar(_get_field(quote, "ask_size")),
        "timestamp": _serialize_scalar(_get_field(quote, "timestamp"))
        or _serialize_scalar(_get_field(quote, "ask_timestamp"))
        or _serialize_scalar(_get_field(quote, "bid_timestamp"))
        or "",
    }


def _serialize_option_contract(contract: Any) -> dict[str, Any]:
    return {
        "contract_id": _serialize_scalar(_get_field(contract, "id")),
        "symbol": str(_get_field(contract, "symbol") or ""),
        "underlying_symbol": str(_get_field(contract, "underlying_symbol") or ""),
        "contract_type": _serialize_scalar(_get_field(contract, "type")) or "",
        "expiration_date": _serialize_scalar(_get_field(contract, "expiration_date")) or "",
        "strike_price": _safe_float(_get_field(contract, "strike_price")),
        "style": _serialize_scalar(_get_field(contract, "style")) or "",
        "status": _serialize_scalar(_get_field(contract, "status")) or "",
        "size": _serialize_scalar(_get_field(contract, "size")),
        "open_interest": _safe_float(_get_field(contract, "open_interest")),
        "open_interest_date": _serialize_scalar(_get_field(contract, "open_interest_date")) or "",
        "close_price": _safe_float(_get_field(contract, "close_price")),
    }


def _format_strike_filter(value: float | None) -> str | None:
    if value is None:
        return None
    normalized = round(float(value), 2)
    return f"{normalized:.2f}".rstrip("0").rstrip(".")


def _build_contract_request(
    *,
    company_symbol: str,
    contract_type: Any,
    expiration_date: str | None,
    expiration_date_gte: str | None,
    expiration_date_lte: str | None,
    strike_price_gte: float | None,
    strike_price_lte: float | None,
    limit: int,
) -> Any:
    return GetOptionContractsRequest(
        underlying_symbols=[company_symbol],
        root_symbol=company_symbol,
        type=contract_type,
        expiration_date=expiration_date,
        expiration_date_gte=expiration_date_gte,
        expiration_date_lte=expiration_date_lte,
        strike_price_gte=_format_strike_filter(strike_price_gte),
        strike_price_lte=_format_strike_filter(strike_price_lte),
        limit=limit,
    )


def _build_contract_request_debug(
    *,
    company_symbol: str,
    contract_type: Any,
    expiration_date: str | None,
    expiration_date_gte: str | None,
    expiration_date_lte: str | None,
    strike_price_gte: float | None,
    strike_price_lte: float | None,
    limit: int,
) -> dict[str, Any]:
    return {
        "underlying_symbols": [company_symbol],
        "root_symbol": company_symbol,
        "type": _serialize_scalar(contract_type),
        "expiration_date": expiration_date or "",
        "expiration_date_gte": expiration_date_gte or "",
        "expiration_date_lte": expiration_date_lte or "",
        "strike_price_gte": _format_strike_filter(strike_price_gte) or "",
        "strike_price_lte": _format_strike_filter(strike_price_lte) or "",
        "limit": limit,
        "paper": _env_flag("ALPACA_PAPER", True),
    }


def _normalize_contract_type(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    if enum_value not in (None, ""):
        contract_type = str(enum_value).strip().lower()
    else:
        contract_type = str(value or "").strip().lower()

    if "." in contract_type:
        contract_type = contract_type.rsplit(".", 1)[-1]

    replacements = {
        "c": "call",
        "call_option": "call",
        "call option": "call",
        "calls": "call",
        "p": "put",
        "put_option": "put",
        "put option": "put",
        "puts": "put",
    }
    contract_type = replacements.get(contract_type, contract_type)
    return contract_type if contract_type in {"call", "put"} else ""


def _contract_preference_key(contract: dict[str, Any], target_strike: float | None) -> tuple[float, str, float, int]:
    strike_price = _safe_float(contract.get("strike_price"))
    strike_distance = (
        abs(strike_price - target_strike)
        if strike_price is not None and target_strike is not None
        else float("inf")
    )
    expiration_date = str(contract.get("expiration_date") or "9999-12-31")
    open_interest = _safe_float(contract.get("open_interest"))
    open_interest_rank = -(open_interest if open_interest is not None else -1.0)
    option_id = int(contract.get("option_id") or 10**9)

    return (strike_distance, expiration_date, open_interest_rank, option_id)


def _select_contract_subset_near_reference(
    contracts: list[dict[str, Any]],
    *,
    reference_stock_price: float | None,
    per_type_limit: int,
) -> list[dict[str, Any]]:
    """Keep a compact, deterministic contract subset around the reference price."""
    if reference_stock_price is None:
        return []

    limit = max(1, int(per_type_limit))
    call_target = reference_stock_price + PREFERRED_OTM_DISTANCE
    put_target = reference_stock_price - PREFERRED_OTM_DISTANCE

    all_calls = [
        contract
        for contract in contracts
        if _normalize_contract_type(contract.get("contract_type")) == "call"
        and (_safe_float(contract.get("strike_price")) is not None)
        and (_safe_float(contract.get("strike_price")) >= reference_stock_price)
    ]
    all_puts = [
        contract
        for contract in contracts
        if _normalize_contract_type(contract.get("contract_type")) == "put"
        and (_safe_float(contract.get("strike_price")) is not None)
        and (_safe_float(contract.get("strike_price")) <= reference_stock_price)
    ]

    preferred_calls = [
        contract
        for contract in all_calls
        if (_safe_float(contract.get("strike_price")) or 0.0) >= call_target
    ]
    preferred_puts = [
        contract
        for contract in all_puts
        if (_safe_float(contract.get("strike_price")) or 0.0) <= put_target
    ]

    if preferred_calls:
        preferred_calls.sort(key=lambda contract: _contract_preference_key(contract, call_target))
        selected_calls = preferred_calls[:limit]
    else:
        all_calls.sort(
            key=lambda contract: (
                abs((_safe_float(contract.get("strike_price")) or 0.0) - reference_stock_price),
                str(contract.get("expiration_date") or "9999-12-31"),
                -(_safe_float(contract.get("open_interest")) or -1.0),
                int(contract.get("option_id") or 10**9),
            )
        )
        selected_calls = all_calls[:limit]

    if preferred_puts:
        preferred_puts.sort(key=lambda contract: _contract_preference_key(contract, put_target))
        selected_puts = preferred_puts[:limit]
    else:
        all_puts.sort(
            key=lambda contract: (
                abs((_safe_float(contract.get("strike_price")) or 0.0) - reference_stock_price),
                str(contract.get("expiration_date") or "9999-12-31"),
                -(_safe_float(contract.get("open_interest")) or -1.0),
                int(contract.get("option_id") or 10**9),
            )
        )
        selected_puts = all_puts[:limit]

    return selected_calls + selected_puts


def _fetch_option_contracts(
    company_symbol: str,
    *,
    contract_type: Any,
    expiration_date: str | None,
    expiration_date_gte: str | None,
    expiration_date_lte: str | None,
    strike_price_gte: float | None,
    strike_price_lte: float | None,
    limit: int,
) -> list[Any]:
    clients = _get_alpaca_clients()
    if clients is None or GetOptionContractsRequest is None:
        return []

    response = clients["trading"].get_option_contracts(
        _build_contract_request(
            company_symbol=company_symbol,
            contract_type=contract_type,
            expiration_date=expiration_date,
            expiration_date_gte=expiration_date_gte,
            expiration_date_lte=expiration_date_lte,
            strike_price_gte=strike_price_gte,
            strike_price_lte=strike_price_lte,
            limit=limit,
        )
    )
    return _extract_contract_items(response)


def _normalize_option_snapshot_map(chain_response: Any) -> dict[str, Any]:
    if isinstance(chain_response, dict):
        return {str(symbol): snapshot for symbol, snapshot in chain_response.items()}

    try:
        iterator = dict(chain_response)
    except (TypeError, ValueError):
        return {}

    return {str(symbol): snapshot for symbol, snapshot in iterator.items()}


def _build_option_market_snapshot(
    company_symbol: str,
    *,
    reference_stock_price: float | None,
    expiration_date: str | None,
    expiration_date_gte: str | None,
    expiration_date_lte: str | None,
    strike_price_gte: float | None,
    strike_price_lte: float | None,
    contract_limit_per_type: int,
) -> dict[str, Any]:
    unavailable = {
        "available": False,
        "underlying_symbol": company_symbol,
        "selection_filters": {
            "expiration_date": expiration_date or "",
            "expiration_date_gte": expiration_date_gte or "",
            "expiration_date_lte": expiration_date_lte or "",
            "strike_price_gte": strike_price_gte,
            "strike_price_lte": strike_price_lte,
            "contract_limit_per_type": contract_limit_per_type,
        },
        "contract_count": 0,
        "available_expirations": [],
        "available_strikes": [],
        "contracts": [],
    }

    clients = _get_alpaca_clients()
    if clients is None or ContractType is None or OptionChainRequest is None:
        unavailable["error"] = (
            f"alpaca-py is unavailable: {ALPACA_IMPORT_ERROR}"
            if ALPACA_IMPORT_ERROR is not None
            else "Alpaca credentials were not configured."
        )
        return unavailable

    try:
        raw_fetch_limit = max(
            contract_limit_per_type * DEFAULT_OPTION_CHAIN_FETCH_MULTIPLIER,
            DEFAULT_OPTION_FETCH_MIN,
        )

        effective_strike_price_gte = strike_price_gte
        effective_strike_price_lte = strike_price_lte
        if reference_stock_price is not None:
            if effective_strike_price_gte is None:
                effective_strike_price_gte = max(0.0, reference_stock_price - 25.0)
            if effective_strike_price_lte is None:
                effective_strike_price_lte = reference_stock_price + 25.0

        effective_expiration_date = expiration_date
        effective_expiration_date_gte = expiration_date_gte
        effective_expiration_date_lte = expiration_date_lte
        if not effective_expiration_date and not effective_expiration_date_gte and not effective_expiration_date_lte:
            today = date.today()
            effective_expiration_date_gte = (today.fromordinal(today.toordinal() + CLOSEST_EXPIRATION_GTE)).isoformat()
            effective_expiration_date_lte = (today.fromordinal(today.toordinal() + FARTHEST_EXPIRATION_LTE)).isoformat()

        call_request_debug = _build_contract_request_debug(
            company_symbol=company_symbol,
            contract_type=ContractType.CALL,
            expiration_date=effective_expiration_date,
            expiration_date_gte=effective_expiration_date_gte,
            expiration_date_lte=effective_expiration_date_lte,
            strike_price_gte=effective_strike_price_gte,
            strike_price_lte=effective_strike_price_lte,
            limit=raw_fetch_limit,
        )
        put_request_debug = _build_contract_request_debug(
            company_symbol=company_symbol,
            contract_type=ContractType.PUT,
            expiration_date=effective_expiration_date,
            expiration_date_gte=effective_expiration_date_gte,
            expiration_date_lte=effective_expiration_date_lte,
            strike_price_gte=effective_strike_price_gte,
            strike_price_lte=effective_strike_price_lte,
            limit=raw_fetch_limit,
        )

        LOGGER.info(
            "Option contract lookup for %s using call_request=%s put_request=%s",
            company_symbol,
            call_request_debug,
            put_request_debug,
        )

        call_contracts = _fetch_option_contracts(
            company_symbol,
            contract_type=ContractType.CALL,
            expiration_date=effective_expiration_date,
            expiration_date_gte=effective_expiration_date_gte,
            expiration_date_lte=effective_expiration_date_lte,
            strike_price_gte=effective_strike_price_gte,
            strike_price_lte=effective_strike_price_lte,
            limit=raw_fetch_limit,
        )
        put_contracts = _fetch_option_contracts(
            company_symbol,
            contract_type=ContractType.PUT,
            expiration_date=effective_expiration_date,
            expiration_date_gte=effective_expiration_date_gte,
            expiration_date_lte=effective_expiration_date_lte,
            strike_price_gte=effective_strike_price_gte,
            strike_price_lte=effective_strike_price_lte,
            limit=raw_fetch_limit,
        )
    except Exception as exc:
        unavailable["error"] = str(exc)
        LOGGER.warning(
            "Option market lookup failed for %s with error=%s call_request=%s put_request=%s",
            company_symbol,
            unavailable["error"],
            locals().get("call_request_debug", {}),
            locals().get("put_request_debug", {}),
        )
        return unavailable

    contract_map: dict[str, Any] = {}
    for contract in call_contracts + put_contracts:
        symbol = str(_get_field(contract, "symbol") or "").strip().upper()
        if symbol:
            contract_map[symbol] = contract

    if not contract_map:
        unavailable["error"] = (
            f"No matching option contracts were returned for {company_symbol} "
            f"within expiration window {effective_expiration_date or ''} "
            f"{effective_expiration_date_gte or ''} {effective_expiration_date_lte or ''}."
        )
        unavailable["selection_filters"] = {
            **unavailable["selection_filters"],
            "effective_expiration_date": effective_expiration_date or "",
            "effective_expiration_date_gte": effective_expiration_date_gte or "",
            "effective_expiration_date_lte": effective_expiration_date_lte or "",
            "effective_strike_price_gte": effective_strike_price_gte,
            "effective_strike_price_lte": effective_strike_price_lte,
        }
        unavailable["request_debug"] = {
            "call_request": call_request_debug,
            "put_request": put_request_debug,
        }
        LOGGER.warning(
            "Option market returned no contracts for %s. option_market.error=%s call_request=%s put_request=%s",
            company_symbol,
            unavailable["error"],
            call_request_debug,
            put_request_debug,
        )
        return unavailable

    snapshots_by_symbol: dict[str, Any] = {}
    chain_error = ""
    try:
        chain_response = clients["option"].get_option_chain(
            OptionChainRequest(
                underlying_symbol=company_symbol,
                expiration_date=effective_expiration_date,
                expiration_date_gte=effective_expiration_date_gte,
                expiration_date_lte=effective_expiration_date_lte,
                strike_price_gte=effective_strike_price_gte,
                strike_price_lte=effective_strike_price_lte,
            )
        )
        snapshots_by_symbol = _normalize_option_snapshot_map(chain_response)
    except Exception as exc:
        chain_error = str(exc)

    serialized_contracts: list[dict[str, Any]] = []
    expiration_values: set[str] = set()
    strike_values: set[float] = set()

    for symbol, contract in contract_map.items():
        serialized = _serialize_option_contract(contract)
        snapshot = snapshots_by_symbol.get(symbol)
        if snapshot is not None:
            serialized["implied_volatility"] = _safe_float(_get_field(snapshot, "implied_volatility"))
            serialized["greeks"] = _serialize_greeks(_get_field(snapshot, "greeks"))
            serialized["latest_quote"] = _serialize_quote_snapshot(_get_field(snapshot, "latest_quote"))
            serialized["latest_trade_price"] = _safe_float(_get_field(_get_field(snapshot, "latest_trade"), "price"))
        else:
            serialized["implied_volatility"] = None
            serialized["greeks"] = _serialize_greeks(None)
            serialized["latest_quote"] = _serialize_quote_snapshot(None)
            serialized["latest_trade_price"] = None

        if serialized["expiration_date"]:
            expiration_values.add(str(serialized["expiration_date"]))
        if serialized["strike_price"] is not None:
            strike_values.add(float(serialized["strike_price"]))

        serialized_contracts.append(serialized)

    serialized_contracts.sort(
        key=lambda item: (
            str(item.get("expiration_date") or ""),
            float(item.get("strike_price") or 0),
            str(item.get("contract_type") or ""),
            str(item.get("symbol") or ""),
        )
    )
    for index, contract in enumerate(serialized_contracts, start=1):
        contract["option_id"] = index

    selected_contracts = _select_contract_subset_near_reference(
        serialized_contracts,
        reference_stock_price=reference_stock_price,
        per_type_limit=contract_limit_per_type,
    )

    payload = {
        "available": True,
        "underlying_symbol": company_symbol,
        "selection_filters": {
            **unavailable["selection_filters"],
            "effective_expiration_date": effective_expiration_date or "",
            "effective_expiration_date_gte": effective_expiration_date_gte or "",
            "effective_expiration_date_lte": effective_expiration_date_lte or "",
            "effective_strike_price_gte": effective_strike_price_gte,
            "effective_strike_price_lte": effective_strike_price_lte,
        },
        "reference_stock_price": reference_stock_price,
        "raw_contract_count": len(serialized_contracts),
        "contract_count": len(selected_contracts),
        "available_expirations": sorted(expiration_values),
        "available_strikes": sorted(strike_values),
        "contracts": selected_contracts,
        "request_debug": {
            "call_request": call_request_debug,
            "put_request": put_request_debug,
        },
    }
    if chain_error:
        payload["warning"] = f"Option chain snapshots were unavailable: {chain_error}"
    return payload


def _serialize_position(position: Any) -> dict[str, Any]:
    return {
        "symbol": str(_get_field(position, "symbol") or ""),
        "asset_class": _serialize_scalar(_get_field(position, "asset_class")) or "",
        "side": _serialize_scalar(_get_field(position, "side")) or "",
        "qty": _safe_float(_get_field(position, "qty")),
        "avg_entry_price": _safe_float(_get_field(position, "avg_entry_price")),
        "current_price": _safe_float(_get_field(position, "current_price")),
        "market_value": _safe_float(_get_field(position, "market_value")),
        "cost_basis": _safe_float(_get_field(position, "cost_basis")),
        "unrealized_pl": _safe_float(_get_field(position, "unrealized_pl")),
        "unrealized_plpc": _safe_float(_get_field(position, "unrealized_plpc")),
        "change_today": _safe_float(_get_field(position, "change_today")),
    }


def _build_account_state(company_symbol: str) -> dict[str, Any]:
    unavailable = {
        "available": False,
        "buying_power": None,
        "options_buying_power": None,
        "cash": None,
        "equity": None,
        "portfolio_value": None,
        "trading_blocked": None,
        "account_blocked": None,
        "options_approved_level": None,
        "options_trading_level": None,
        "company_position_state": {
            "matching_position_count": 0,
            "has_stock_position": False,
            "has_option_positions": False,
            "positions": [],
        },
    }

    clients = _get_alpaca_clients()
    if clients is None:
        unavailable["error"] = (
            f"alpaca-py is unavailable: {ALPACA_IMPORT_ERROR}"
            if ALPACA_IMPORT_ERROR is not None
            else "Alpaca credentials were not configured."
        )
        return unavailable

    try:
        account = clients["trading"].get_account()
        positions = clients["trading"].get_all_positions()
    except Exception as exc:
        unavailable["error"] = str(exc)
        return unavailable

    option_pattern = re.compile(rf"^{re.escape(company_symbol)}{OPTION_SYMBOL_TEMPLATE}")
    matching_positions: list[dict[str, Any]] = []
    has_stock_position = False
    has_option_positions = False

    for position in positions or []:
        symbol = str(_get_field(position, "symbol") or "").strip().upper()
        if symbol == company_symbol:
            has_stock_position = True
            matching_positions.append(_serialize_position(position))
            continue
        if option_pattern.match(symbol):
            has_option_positions = True
            matching_positions.append(_serialize_position(position))

    return {
        "available": True,
        "buying_power": _safe_float(_get_field(account, "buying_power")),
        "options_buying_power": _safe_float(_get_field(account, "options_buying_power")),
        "cash": _safe_float(_get_field(account, "cash")),
        "equity": _safe_float(_get_field(account, "equity")),
        "portfolio_value": _safe_float(_get_field(account, "portfolio_value")),
        "daytrading_buying_power": _safe_float(_get_field(account, "daytrading_buying_power")),
        "regt_buying_power": _safe_float(_get_field(account, "regt_buying_power")),
        "trading_blocked": _get_field(account, "trading_blocked"),
        "account_blocked": _get_field(account, "account_blocked"),
        "shorting_enabled": _get_field(account, "shorting_enabled"),
        "options_approved_level": _serialize_scalar(_get_field(account, "options_approved_level")),
        "options_trading_level": _serialize_scalar(_get_field(account, "options_trading_level")),
        "company_position_state": {
            "matching_position_count": len(matching_positions),
            "has_stock_position": has_stock_position,
            "has_option_positions": has_option_positions,
            "positions": matching_positions,
        },
    }


def build_market_context(
    company: dict[str, Any],
    *,
    option_expiration_date: str | None,
    option_expiration_date_gte: str | None,
    option_expiration_date_lte: str | None,
    option_strike_price_gte: float | None,
    option_strike_price_lte: float | None,
    option_contract_limit_per_type: int,
) -> dict[str, Any]:
    """Build the live market/account block used by the manager stage.

    Usage:
        ``build_market_context(company, option_expiration_date=None, ... )``

    The returned payload is intentionally JSON-friendly so it can be passed
    straight into the manager prompt or stored for diagnostics.
    """
    company_symbol = str(company.get("symbol") or "").strip().upper()
    stock_snapshot = _build_current_stock_price_snapshot(company)
    reference_stock_price = _get_reference_stock_price_from_snapshot(stock_snapshot)

    return {
        "current_stock_price": stock_snapshot,
        "market_indices": _build_market_indices_snapshot(),
        "option_market": _build_option_market_snapshot(
            company_symbol,
            reference_stock_price=reference_stock_price,
            expiration_date=option_expiration_date,
            expiration_date_gte=option_expiration_date_gte,
            expiration_date_lte=option_expiration_date_lte,
            strike_price_gte=option_strike_price_gte,
            strike_price_lte=option_strike_price_lte,
            contract_limit_per_type=option_contract_limit_per_type,
        ),
        "account_state": _build_account_state(company_symbol),
    }


def _run_reference_price_smoke_test(symbol: str) -> int:
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        print("Usage: python market_context.py <SYMBOL>")
        return 1

    company = {"symbol": normalized_symbol}
    stock_snapshot = _build_current_stock_price_snapshot(company)
    reference_stock_price = _get_reference_stock_price_from_snapshot(stock_snapshot)

    print(f"symbol: {normalized_symbol}")
    print(f"snapshot_source: {stock_snapshot.get('source')}")
    print(f"snapshot_timestamp: {stock_snapshot.get('timestamp')}")
    print(f"latest_trade_timestamp: {stock_snapshot.get('latest_trade_timestamp')}")
    print(f"latest_trade_price: {stock_snapshot.get('latest_trade_price')}")
    print(f"price: {stock_snapshot.get('price')}")
    print(f"bid_price: {stock_snapshot.get('bid_price')}")
    print(f"ask_price: {stock_snapshot.get('ask_price')}")
    print(f"midpoint_price: {stock_snapshot.get('midpoint_price')}")
    print(f"reference_stock_price: {reference_stock_price}")
    print(f"available: {stock_snapshot.get('available')}")
    print(f"error: {stock_snapshot.get('error', '')}")
    print("snapshot_json:")
    print(json.dumps(stock_snapshot, indent=2, sort_keys=True))

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Smoke-test the Alpaca-backed stock reference price calculation."
    )
    parser.add_argument("symbol", nargs="?", help="Ticker symbol to inspect, e.g. SHOP")
    args = parser.parse_args()
    raise SystemExit(_run_reference_price_smoke_test(args.symbol or "AAPL"))
