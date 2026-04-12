from __future__ import annotations

from datetime import date, datetime
import os
from pathlib import Path
import re
import sys
from typing import Any


AGENT_HELPERS_DIR = Path(__file__).resolve().parent
AGENT_CALLERS_DIR = AGENT_HELPERS_DIR.parent
PYTHON_SCRIPTS_DIR = AGENT_CALLERS_DIR.parent
ROOT_DIR = PYTHON_SCRIPTS_DIR.parent
DATA_DIR = ROOT_DIR / "Data"
ENV_PATH = ROOT_DIR / ".env"

for path in (AGENT_CALLERS_DIR, PYTHON_SCRIPTS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.append(normalized)

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(ENV_PATH)

try:
    from alpaca.data import OptionHistoricalDataClient, StockHistoricalDataClient
    from alpaca.data.requests import OptionChainRequest, StockLatestQuoteRequest
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import ContractType
    from alpaca.trading.requests import GetOptionContractsRequest

    ALPACA_IMPORT_ERROR: Exception | None = None
except ImportError as exc:
    OptionHistoricalDataClient = None
    OptionChainRequest = None
    StockHistoricalDataClient = None
    StockLatestQuoteRequest = None
    TradingClient = None
    ContractType = None
    GetOptionContractsRequest = None
    ALPACA_IMPORT_ERROR = exc

from StrategistPayloadBuilder import (
    DEFAULT_FULL_ARTICLE_LIMIT,
    DEFAULT_MAX_ARTICLE_AGE_DAYS,
    DEFAULT_SUMMARY_ARTICLE_LIMIT,
    build_strategist_input,
)


DEFAULT_OPTION_CHAIN_LIMIT_PER_TYPE = max(1, int(os.getenv("MANAGER_OPTION_CHAIN_LIMIT_PER_TYPE", "6")))
OPTION_SYMBOL_TEMPLATE = r"\d{6}[CP]\d{8}$"
_ALPACA_CLIENTS: dict[str, Any] | None | bool = None

__all__ = [
    "DEFAULT_FULL_ARTICLE_LIMIT",
    "DEFAULT_MAX_ARTICLE_AGE_DAYS",
    "DEFAULT_OPTION_CHAIN_LIMIT_PER_TYPE",
    "DEFAULT_SUMMARY_ARTICLE_LIMIT",
    "build_manager_input",
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


def _serialize_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()

    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float, bool)) or enum_value is None:
        if enum_value is not None:
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
        "bid_price": None,
        "ask_price": None,
        "midpoint_price": None,
        "timestamp": "",
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
        quotes = clients["stock"].get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=symbol)
        )
        quote = quotes.get(symbol) if hasattr(quotes, "get") else None
        if quote is None:
            fallback["error"] = f"No latest stock quote was returned for {symbol}."
            return fallback

        bid_price = _safe_float(_get_field(quote, "bid_price"))
        ask_price = _safe_float(_get_field(quote, "ask_price"))
        midpoint_price = None
        if bid_price is not None and ask_price is not None:
            midpoint_price = round((bid_price + ask_price) / 2.0, 4)

        price = ask_price if ask_price is not None else bid_price
        if price is None:
            price = midpoint_price

        return {
            "available": price is not None,
            "symbol": symbol,
            "source": "alpaca_latest_quote",
            "price": price,
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
        strike_price_gte=strike_price_gte,
        strike_price_lte=strike_price_lte,
        limit=limit,
    )


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
        return {
            str(symbol): snapshot
            for symbol, snapshot in chain_response.items()
        }

    normalized: dict[str, Any] = {}
    try:
        iterator = dict(chain_response)
    except (TypeError, ValueError):
        return normalized

    for symbol, snapshot in iterator.items():
        normalized[str(symbol)] = snapshot
    return normalized


def _build_option_market_snapshot(
    company_symbol: str,
    *,
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
        call_contracts = _fetch_option_contracts(
            company_symbol,
            contract_type=ContractType.CALL,
            expiration_date=expiration_date,
            expiration_date_gte=expiration_date_gte,
            expiration_date_lte=expiration_date_lte,
            strike_price_gte=strike_price_gte,
            strike_price_lte=strike_price_lte,
            limit=contract_limit_per_type,
        )
        put_contracts = _fetch_option_contracts(
            company_symbol,
            contract_type=ContractType.PUT,
            expiration_date=expiration_date,
            expiration_date_gte=expiration_date_gte,
            expiration_date_lte=expiration_date_lte,
            strike_price_gte=strike_price_gte,
            strike_price_lte=strike_price_lte,
            limit=contract_limit_per_type,
        )
    except Exception as exc:
        unavailable["error"] = str(exc)
        return unavailable

    contract_map: dict[str, Any] = {}
    for contract in call_contracts + put_contracts:
        symbol = str(_get_field(contract, "symbol") or "").strip().upper()
        if symbol:
            contract_map[symbol] = contract

    if not contract_map:
        unavailable["error"] = f"No matching option contracts were returned for {company_symbol}."
        return unavailable

    snapshots_by_symbol: dict[str, Any] = {}
    chain_error = ""
    try:
        chain_response = clients["option"].get_option_chain(
            OptionChainRequest(
                underlying_symbol=company_symbol,
                expiration_date=expiration_date,
                expiration_date_gte=expiration_date_gte,
                expiration_date_lte=expiration_date_lte,
                strike_price_gte=strike_price_gte,
                strike_price_lte=strike_price_lte,
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

    payload = {
        "available": True,
        "underlying_symbol": company_symbol,
        "selection_filters": unavailable["selection_filters"],
        "contract_count": len(serialized_contracts),
        "available_expirations": sorted(expiration_values),
        "available_strikes": sorted(strike_values),
        "contracts": serialized_contracts,
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
    company_symbol = str(company.get("symbol") or "").strip().upper()
    return {
        "current_stock_price": _build_current_stock_price_snapshot(company),
        "option_market": _build_option_market_snapshot(
            company_symbol,
            expiration_date=option_expiration_date,
            expiration_date_gte=option_expiration_date_gte,
            expiration_date_lte=option_expiration_date_lte,
            strike_price_gte=option_strike_price_gte,
            strike_price_lte=option_strike_price_lte,
            contract_limit_per_type=option_contract_limit_per_type,
        ),
        "account_state": _build_account_state(company_symbol),
    }


def build_manager_input(
    company_identifier: str,
    *,
    start_time: datetime | None,
    end_time: datetime | None,
    max_age_days: int | None,
    summary_article_limit: int,
    full_article_limit: int,
    option_expiration_date: str | None,
    option_expiration_date_gte: str | None,
    option_expiration_date_lte: str | None,
    option_strike_price_gte: float | None,
    option_strike_price_lte: float | None,
    option_contract_limit_per_type: int,
) -> dict[str, Any]:
    payload = build_strategist_input(
        company_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
        summary_article_limit=summary_article_limit,
        full_article_limit=full_article_limit,
    )
    payload["market_context"] = build_market_context(
        payload["company"],
        option_expiration_date=option_expiration_date,
        option_expiration_date_gte=option_expiration_date_gte,
        option_expiration_date_lte=option_expiration_date_lte,
        option_strike_price_gte=option_strike_price_gte,
        option_strike_price_lte=option_strike_price_lte,
        option_contract_limit_per_type=max(1, int(option_contract_limit_per_type)),
    )
    return payload
