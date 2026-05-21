from __future__ import annotations

"""Market-data services used by the manager stage.

This module owns the Alpaca-specific work needed to enrich a strategist payload
with live market context. Keeping that logic here lets ``agent_helpers.manager``
stay focused on orchestration instead of vendor details.
"""

import json
import argparse
from datetime import date, datetime, timedelta, timezone
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

from _paths import DATA_DIR, ROOT_DIR, bootstrap_agent_callers, load_project_env


if __name__ == "__main__":
    load_project_env()

bootstrap_agent_callers(include_webscraping=True)

try:
    from alpaca.data import OptionHistoricalDataClient, StockHistoricalDataClient
    from alpaca.data.requests import (
        OptionChainRequest,
        StockBarsRequest,
        StockLatestQuoteRequest,
        StockLatestTradeRequest,
    )
    from alpaca.data.timeframe import TimeFrame
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import ContractType
    from alpaca.trading.requests import GetOptionContractsRequest

    ALPACA_IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - optional dependency
    OptionHistoricalDataClient = None
    OptionChainRequest = None
    StockBarsRequest = None
    StockHistoricalDataClient = None
    StockLatestQuoteRequest = None
    StockLatestTradeRequest = None
    TimeFrame = None
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

from agent_helpers.volatility import (
    compute_percentile_rank,
    compute_realized_volatility,
    summarize_option_iv,
)


DEFAULT_OPTION_CHAIN_LIMIT_PER_TYPE = max(1, int(os.getenv("MANAGER_OPTION_CHAIN_LIMIT_PER_TYPE", "6")))
DEFAULT_OPTION_CHAIN_FETCH_MULTIPLIER = max(2, int(os.getenv("MANAGER_OPTION_CHAIN_FETCH_MULTIPLIER", "6")))
DEFAULT_OPTION_FETCH_MIN = max(100, int(os.getenv("MANAGER_OPTION_FETCH_MIN", "500")))
PREFERRED_OTM_DISTANCE = 0.75
OPTION_SYMBOL_TEMPLATE = r"\d{6}[CP]\d{8}$"
_ALPACA_CLIENTS: dict[str, Any] | None | bool = None
_SECTOR_ETF_MAP: dict[str, dict[str, Any]] | None = None
MARKET_INDEX_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("sp500", "^GSPC", "S&P 500"),
    ("dow_jones_industrial_average", "^DJI", "Dow Jones Industrial Average"),
    ("vix", "^VIX", "CBOE Volatility Index"),
)
SECTOR_ETF_FILE = DATA_DIR / "sector_etfs.json"

CLOSEST_EXPIRATION_GTE = 1
FARTHEST_EXPIRATION_LTE = 8
UNDERLYING_HISTORY_LOOKBACK_CALENDAR_DAYS = max(
    90,
    int(os.getenv("MANAGER_UNDERLYING_HISTORY_LOOKBACK_DAYS", "120")),
)
UNDERLYING_HV_SHORT_WINDOW_DAYS = max(
    20,
    int(os.getenv("MANAGER_UNDERLYING_HV_SHORT_WINDOW_DAYS", "20")),
)
UNDERLYING_HV_LONG_WINDOW_DAYS = max(
    UNDERLYING_HV_SHORT_WINDOW_DAYS,
    int(os.getenv("MANAGER_UNDERLYING_HV_LONG_WINDOW_DAYS", "60")),
)
OPTION_IV_HISTORY_PATH = Path(
    os.getenv(
        "OPTION_IV_HISTORY_PATH",
        str(ROOT_DIR / "shared" / "option_iv_history.json"),
    )
)
OPTION_IV_HISTORY_MAX_ENTRIES = max(
    30,
    int(os.getenv("OPTION_IV_HISTORY_MAX_ENTRIES", "252")),
)
OPTION_IV_BUCKET_MIN_HISTORY_SAMPLES = max(
    1,
    int(os.getenv("OPTION_IV_BUCKET_MIN_HISTORY_SAMPLES", "20")),
)
IV_PERCENTILE_DTE_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("1_3", 1, 3),
    ("4_7", 4, 7),
    ("8_14", 8, 14),
    ("15_30", 15, 30),
    ("31_45", 31, 45),
    ("46_60", 46, 60),
)
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


def _normalize_identifier(value: Any) -> str:
    return str(value or "").strip().lower()


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
    # Assemble the stock fallback snapshot so callers can work from one normalized shape.
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
    # Assemble the market index snapshot so callers can work from one normalized shape.
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


def _load_sector_etf_map() -> dict[str, dict[str, Any]]:
    # Load the sector etf map once so the downstream logic can stay focused on orchestration.
    global _SECTOR_ETF_MAP
    if _SECTOR_ETF_MAP is not None:
        return _SECTOR_ETF_MAP

    try:
        raw_payload = json.loads(SECTOR_ETF_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        LOGGER.warning("Sector ETF file was not found at %s", SECTOR_ETF_FILE)
        _SECTOR_ETF_MAP = {}
        return _SECTOR_ETF_MAP
    except json.JSONDecodeError as exc:
        LOGGER.warning("Sector ETF file at %s could not be decoded: %s", SECTOR_ETF_FILE, exc)
        _SECTOR_ETF_MAP = {}
        return _SECTOR_ETF_MAP

    if not isinstance(raw_payload, dict):
        LOGGER.warning("Sector ETF file at %s did not contain a JSON object", SECTOR_ETF_FILE)
        _SECTOR_ETF_MAP = {}
        return _SECTOR_ETF_MAP

    normalized_payload: dict[str, dict[str, Any]] = {}
    for raw_key, raw_value in raw_payload.items():
        normalized_key = _normalize_identifier(raw_key)
        if not normalized_key or not isinstance(raw_value, dict):
            continue
        normalized_payload[normalized_key] = raw_value

    _SECTOR_ETF_MAP = normalized_payload
    return _SECTOR_ETF_MAP


def _resolve_company_sector_etf(company: dict[str, Any]) -> tuple[str, dict[str, Any], str] | None:
    # Resolve the company sector etf into the concrete values the caller should use.
    sector_etf_map = _load_sector_etf_map()
    if not sector_etf_map:
        return None

    sector_key = _normalize_identifier(company.get("sector_key"))
    if sector_key:
        etf_entry = sector_etf_map.get(sector_key)
        if isinstance(etf_entry, dict):
            return sector_key, etf_entry, "sector_key"

    sector_name = _normalize_identifier(company.get("sector_name"))
    if not sector_name:
        return None

    for etf_key, etf_entry in sector_etf_map.items():
        if not isinstance(etf_entry, dict):
            continue
        if sector_name == _normalize_identifier(etf_entry.get("name")):
            return etf_key, etf_entry, "sector_name"

    return None


def _build_sector_etf_snapshot(company: dict[str, Any]) -> dict[str, Any]:
    # Assemble the sector etf snapshot so callers can work from one normalized shape.
    sector_key = _normalize_identifier(company.get("sector_key"))
    sector_name = str(company.get("sector_name") or "").strip()
    unavailable = {
        "available": False,
        "sector_key": sector_key,
        "sector_name": sector_name,
        "symbol": "",
        "ticker": "",
        "label": "",
        "fund_name": "",
        "provider": "",
        "category": "sector_etf",
        "source": "yfinance",
        "price": None,
        "previous_close": None,
        "absolute_change": None,
        "percent_change": None,
        "timestamp": "",
        "matched_on": "",
        "error": "",
    }

    resolved = _resolve_company_sector_etf(company)
    if resolved is None:
        unavailable["error"] = (
            "No sector ETF mapping was found for the company sector."
            if sector_key or sector_name
            else "Company sector metadata was missing."
        )
        return unavailable

    resolved_sector_key, etf_entry, matched_on = resolved
    ticker = str(etf_entry.get("ticker") or "").strip().upper()
    label = str(etf_entry.get("fund_name") or etf_entry.get("name") or "").strip()
    snapshot = _build_market_index_snapshot(symbol=ticker, label=label)
    snapshot.update(
        {
            "sector_key": resolved_sector_key,
            "sector_name": str(etf_entry.get("name") or sector_name or "").strip(),
            "ticker": ticker,
            "fund_name": str(etf_entry.get("fund_name") or "").strip(),
            "provider": str(etf_entry.get("provider") or "").strip(),
            "category": str(etf_entry.get("category") or "sector_etf").strip(),
            "matched_on": matched_on,
        }
    )
    return snapshot


def _build_market_indices_snapshot() -> dict[str, Any]:
    return {
        key: _build_market_index_snapshot(symbol=symbol, label=label)
        for key, symbol, label in MARKET_INDEX_DEFINITIONS
    }


def _build_current_stock_price_snapshot(company: dict[str, Any]) -> dict[str, Any]:
    # Assemble the current stock price snapshot so callers can work from one normalized shape.
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


def _empty_underlying_price_history_snapshot(*, symbol: str, error: str = "") -> dict[str, Any]:
    return {
        "available": False,
        "symbol": symbol,
        "source": "alpaca_stock_bars",
        "timeframe": "1Day",
        "lookback_calendar_days": UNDERLYING_HISTORY_LOOKBACK_CALENDAR_DAYS,
        "close_count": 0,
        "first_bar_timestamp": "",
        "last_bar_timestamp": "",
        "first_close": None,
        "last_close": None,
        "historical_volatility_20d": None,
        "historical_volatility_60d": None,
        "recent_closes": [],
        "error": error,
    }


def _load_option_iv_history_store() -> dict[str, Any]:
    if not OPTION_IV_HISTORY_PATH.exists():
        return {"version": 1, "underlyings": {}}

    try:
        raw_payload = json.loads(OPTION_IV_HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "underlyings": {}}

    if not isinstance(raw_payload, dict):
        return {"version": 1, "underlyings": {}}

    underlyings = raw_payload.get("underlyings", {})
    if not isinstance(underlyings, dict):
        underlyings = {}
    return {
        "version": int(raw_payload.get("version") or 1),
        "underlyings": underlyings,
    }


def _save_option_iv_history_store(store: dict[str, Any]) -> None:
    try:
        OPTION_IV_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        OPTION_IV_HISTORY_PATH.write_text(
            json.dumps(store, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
    except OSError:
        LOGGER.warning("Failed to write option IV history to %s", OPTION_IV_HISTORY_PATH)


def _normalize_iv_history_entries(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        return []

    normalized_entries: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        recorded_at = str(entry.get("recorded_at") or "").strip()
        value = _safe_float(entry.get("value"))
        if not recorded_at or value is None:
            continue
        normalized_entries.append(
            {
                "recorded_at": recorded_at,
                "value": round(value, 6),
            }
        )
    normalized_entries.sort(key=lambda item: str(item["recorded_at"]))
    return normalized_entries[-OPTION_IV_HISTORY_MAX_ENTRIES:]


def _upsert_iv_history_entry(
    entries: list[dict[str, Any]],
    *,
    recorded_at: str,
    value: float,
) -> list[dict[str, Any]]:
    normalized_entries = _normalize_iv_history_entries(entries)
    updated = False
    for entry in normalized_entries:
        if str(entry.get("recorded_at") or "").strip() == str(recorded_at).strip():
            entry["recorded_at"] = recorded_at
            entry["value"] = round(value, 6)
            updated = True
            break
    if not updated:
        normalized_entries.append(
            {
                "recorded_at": recorded_at,
                "value": round(value, 6),
            }
        )
    normalized_entries.sort(key=lambda item: str(item["recorded_at"]))
    return normalized_entries[-OPTION_IV_HISTORY_MAX_ENTRIES:]


def _calculate_days_to_expiration(expiration_date: str | None) -> int | None:
    expiration_text = str(expiration_date or "").strip()
    if not expiration_text:
        return None

    try:
        expiration = date.fromisoformat(expiration_text)
    except ValueError:
        return None
    return (expiration - date.today()).days


def _resolve_iv_percentile_dte_bucket(days_to_expiration: int | None) -> str | None:
    if days_to_expiration is None or days_to_expiration < 1:
        return None

    for bucket_key, min_days, max_days in IV_PERCENTILE_DTE_BUCKETS:
        if min_days <= days_to_expiration <= max_days:
            return bucket_key
    return None


def _summarize_iv_by_dte_bucket(
    contracts: list[dict[str, Any]],
    *,
    reference_stock_price: float | None,
) -> dict[str, dict[str, Any]]:
    grouped_contracts: dict[str, list[dict[str, Any]]] = {}
    for contract in contracts:
        bucket_key = str(contract.get("dte_bucket") or "").strip()
        implied_volatility = _safe_float(contract.get("implied_volatility"))
        if not bucket_key or implied_volatility is None or implied_volatility < 0:
            continue
        grouped_contracts.setdefault(bucket_key, []).append(contract)

    bucket_summary: dict[str, dict[str, Any]] = {}
    for bucket_key, bucket_contracts in grouped_contracts.items():
        ranked_contracts = list(bucket_contracts)
        ranked_contracts.sort(
            key=lambda contract: (
                abs(
                    (_safe_float(contract.get("strike_price")) or reference_stock_price or 0.0)
                    - (reference_stock_price or 0.0)
                )
                if reference_stock_price is not None
                else 0.0,
                str(contract.get("expiration_date") or "9999-12-31"),
                str(contract.get("contract_type") or ""),
                str(contract.get("symbol") or ""),
            )
        )
        representative_contracts = ranked_contracts[: min(4, len(ranked_contracts))]
        representative_ivs = [
            implied_volatility
            for implied_volatility in (
                _safe_float(contract.get("implied_volatility"))
                for contract in representative_contracts
            )
            if implied_volatility is not None
        ]
        if not representative_ivs:
            continue
        bucket_summary[bucket_key] = {
            "atm_mean_iv": round(sum(representative_ivs) / len(representative_ivs), 6),
            "contract_count": len(bucket_contracts),
            "representative_contract_count": len(representative_ivs),
        }
    return bucket_summary


def _annotate_contract_iv_percentiles(
    contracts: list[dict[str, Any]],
    *,
    bucket_percentiles: dict[str, float | None],
    bucket_history_counts: dict[str, int],
) -> None:
    for contract in contracts:
        days_to_expiration = contract.get("days_to_expiration")
        if days_to_expiration is None:
            days_to_expiration = _calculate_days_to_expiration(
                _serialize_scalar(contract.get("expiration_date")) or ""
            )
            contract["days_to_expiration"] = days_to_expiration

        bucket_key = str(contract.get("dte_bucket") or "").strip()
        if not bucket_key:
            bucket_key = _resolve_iv_percentile_dte_bucket(_safe_float(days_to_expiration))
            contract["dte_bucket"] = bucket_key

        bucket_percentile = bucket_percentiles.get(bucket_key)
        history_count = int(bucket_history_counts.get(bucket_key) or 0)
        contract["iv_percentile"] = bucket_percentile
        contract["dte_bucket_iv_percentile"] = bucket_percentile
        if not bucket_key:
            contract["iv_percentile_source"] = "no_dte_bucket"
        elif bucket_percentile is not None:
            contract["iv_percentile_source"] = f"dte_bucket:{bucket_key}"
        elif history_count < OPTION_IV_BUCKET_MIN_HISTORY_SAMPLES:
            contract["iv_percentile_source"] = "insufficient_bucket_history"
        else:
            contract["iv_percentile_source"] = f"dte_bucket:{bucket_key}_unavailable"


def _historical_values_from_iv_entries(entries: list[dict[str, Any]]) -> list[float]:
    return [
        float(entry["value"])
        for entry in _normalize_iv_history_entries(entries)
        if _safe_float(entry.get("value")) is not None
    ]


def _record_option_iv_history(
    underlying_symbol: str,
    *,
    atm_mean_iv: float | None,
    short_term_atm_iv: float | None,
    longer_term_atm_iv: float | None,
    bucket_atm_ivs: dict[str, float] | None,
    recorded_at: str,
) -> dict[str, Any]:
    store = _load_option_iv_history_store()
    underlyings = store.setdefault("underlyings", {})
    symbol_key = str(underlying_symbol or "").strip().upper()
    symbol_history = underlyings.get(symbol_key)
    if not isinstance(symbol_history, dict):
        symbol_history = {}

    metric_map = {
        "atm_mean_iv": atm_mean_iv,
        "short_term_atm_iv": short_term_atm_iv,
        "longer_term_atm_iv": longer_term_atm_iv,
    }
    for metric_name, metric_value in metric_map.items():
        if metric_value is None:
            continue
        metric_history = symbol_history.get(metric_name, [])
        symbol_history[metric_name] = _upsert_iv_history_entry(
            metric_history,
            recorded_at=recorded_at,
            value=metric_value,
        )

    bucket_histories = symbol_history.get("atm_mean_iv_by_dte_bucket", {})
    if not isinstance(bucket_histories, dict):
        bucket_histories = {}
    for bucket_key, bucket_value in (bucket_atm_ivs or {}).items():
        normalized_bucket_key = str(bucket_key or "").strip()
        if not normalized_bucket_key:
            continue
        bucket_history = bucket_histories.get(normalized_bucket_key, [])
        bucket_histories[normalized_bucket_key] = _upsert_iv_history_entry(
            bucket_history,
            recorded_at=recorded_at,
            value=bucket_value,
        )
    symbol_history["atm_mean_iv_by_dte_bucket"] = bucket_histories

    symbol_history["updated_at"] = recorded_at
    underlyings[symbol_key] = symbol_history
    _save_option_iv_history_store(store)
    return symbol_history


def _enrich_with_alpaca_iv_percentiles(
    underlying_symbol: str,
    volatility_summary: dict[str, Any],
    *,
    contracts: list[dict[str, Any]] | None = None,
    reference_stock_price: float | None = None,
) -> dict[str, Any]:
    if not isinstance(volatility_summary, dict):
        return {}

    recorded_at = datetime.now(timezone.utc).isoformat()
    term_structure = volatility_summary.get("term_structure", {})
    if not isinstance(term_structure, dict):
        term_structure = {}

    store = _load_option_iv_history_store()
    underlyings = store.get("underlyings", {})
    symbol_key = str(underlying_symbol or "").strip().upper()
    existing_symbol_history = underlyings.get(symbol_key, {})
    if not isinstance(existing_symbol_history, dict):
        existing_symbol_history = {}

    dte_bucket_iv_summary = _summarize_iv_by_dte_bucket(
        list(contracts or []),
        reference_stock_price=reference_stock_price,
    )
    atm_mean_iv_history = _historical_values_from_iv_entries(existing_symbol_history.get("atm_mean_iv", []))
    short_term_iv_history = _historical_values_from_iv_entries(existing_symbol_history.get("short_term_atm_iv", []))
    longer_term_iv_history = _historical_values_from_iv_entries(existing_symbol_history.get("longer_term_atm_iv", []))
    existing_bucket_histories = existing_symbol_history.get("atm_mean_iv_by_dte_bucket", {})
    if not isinstance(existing_bucket_histories, dict):
        existing_bucket_histories = {}

    bucket_percentiles: dict[str, float | None] = {}
    bucket_history_counts: dict[str, int] = {}
    for bucket_key, bucket_summary in dte_bucket_iv_summary.items():
        bucket_history = _historical_values_from_iv_entries(existing_bucket_histories.get(bucket_key, []))
        bucket_history_counts[bucket_key] = len(bucket_history)
        bucket_percentiles[bucket_key] = (
            compute_percentile_rank(bucket_summary.get("atm_mean_iv"), bucket_history)
            if len(bucket_history) >= OPTION_IV_BUCKET_MIN_HISTORY_SAMPLES
            else None
        )

    enriched_term_structure = dict(term_structure)
    enriched_term_structure["short_term_iv_percentile"] = compute_percentile_rank(
        term_structure.get("short_term_atm_iv"),
        short_term_iv_history,
    )
    enriched_term_structure["longer_term_iv_percentile"] = compute_percentile_rank(
        term_structure.get("longer_term_atm_iv"),
        longer_term_iv_history,
    )
    volatility_summary["term_structure"] = enriched_term_structure
    volatility_summary["atm_iv_percentile"] = compute_percentile_rank(
        volatility_summary.get("atm_mean_iv"),
        atm_mean_iv_history,
    )
    volatility_summary["dte_bucket_iv_summary"] = dte_bucket_iv_summary
    volatility_summary["dte_bucket_iv_percentiles"] = bucket_percentiles
    volatility_summary["dte_bucket_iv_history_counts"] = bucket_history_counts
    if contracts:
        _annotate_contract_iv_percentiles(
            contracts,
            bucket_percentiles=bucket_percentiles,
            bucket_history_counts=bucket_history_counts,
        )

    symbol_history = _record_option_iv_history(
        underlying_symbol,
        atm_mean_iv=_safe_float(volatility_summary.get("atm_mean_iv")),
        short_term_atm_iv=_safe_float(term_structure.get("short_term_atm_iv")),
        longer_term_atm_iv=_safe_float(term_structure.get("longer_term_atm_iv")),
        bucket_atm_ivs={
            bucket_key: bucket_atm_iv
            for bucket_key, bucket_atm_iv in (
                (bucket_key, _safe_float(bucket_summary.get("atm_mean_iv")))
                for bucket_key, bucket_summary in dte_bucket_iv_summary.items()
            )
            if bucket_atm_iv is not None
        },
        recorded_at=recorded_at,
    )

    volatility_summary["iv_percentile_source"] = "alpaca_snapshot_history"
    volatility_summary["iv_history_path"] = str(OPTION_IV_HISTORY_PATH)
    volatility_summary["iv_history_sample_count"] = len(
        _historical_values_from_iv_entries(symbol_history.get("atm_mean_iv", []))
    )
    volatility_summary["iv_history_updated_at"] = str(symbol_history.get("updated_at") or "")
    return volatility_summary


def _extract_bar_close(bar: Any) -> float | None:
    return _first_float(bar, "close", "c")


def _extract_bar_timestamp(bar: Any) -> str:
    return (
        _serialize_scalar(_get_field(bar, "timestamp"))
        or _serialize_scalar(_get_field(bar, "t"))
        or ""
    )


def _normalize_stock_bars_response(response: Any, symbol: str) -> list[Any]:
    normalized_symbol = str(symbol or "").strip().upper()
    candidate_collections = [response, getattr(response, "data", None)]
    for collection in candidate_collections:
        if collection is None:
            continue
        if isinstance(collection, dict):
            bars = collection.get(normalized_symbol) or collection.get(normalized_symbol.lower())
            if bars is None:
                continue
            if isinstance(bars, list):
                return bars
            try:
                return list(bars)
            except TypeError:
                return []
        getter = getattr(collection, "get", None)
        if callable(getter):
            bars = getter(normalized_symbol)
            if bars is None:
                continue
            if isinstance(bars, list):
                return bars
            try:
                return list(bars)
            except TypeError:
                return []
    return []


def _summarize_underlying_price_history(
    symbol: str,
    bars: list[Any],
    *,
    source: str = "alpaca_stock_bars",
) -> dict[str, Any]:
    snapshot = _empty_underlying_price_history_snapshot(symbol=symbol)
    normalized_symbol = str(symbol or "").strip().upper()
    close_values: list[float] = []
    timestamp_values: list[str] = []
    for bar in bars:
        close_price = _extract_bar_close(bar)
        timestamp = _extract_bar_timestamp(bar)
        if close_price is None or close_price <= 0:
            continue
        close_values.append(close_price)
        if timestamp:
            timestamp_values.append(timestamp)

    if not close_values:
        snapshot["error"] = "No valid close prices were returned from Alpaca stock bars."
        return snapshot

    historical_volatility_short = compute_realized_volatility(
        close_values,
        UNDERLYING_HV_SHORT_WINDOW_DAYS,
    )
    historical_volatility_long = compute_realized_volatility(
        close_values,
        UNDERLYING_HV_LONG_WINDOW_DAYS,
    )

    snapshot.update(
        {
            "available": True,
            "symbol": normalized_symbol,
            "source": source,
            "close_count": len(close_values),
            "first_bar_timestamp": timestamp_values[0] if timestamp_values else "",
            "last_bar_timestamp": timestamp_values[-1] if timestamp_values else "",
            "first_close": round(close_values[0], 6),
            "last_close": round(close_values[-1], 6),
            "historical_volatility_20d": historical_volatility_short,
            "historical_volatility_60d": historical_volatility_long,
            "recent_closes": [round(value, 6) for value in close_values[-5:]],
            "error": "",
        }
    )
    return snapshot


def _build_underlying_price_history_snapshot(symbol: str) -> dict[str, Any]:
    normalized_symbol = str(symbol or "").strip().upper()
    unavailable = _empty_underlying_price_history_snapshot(symbol=normalized_symbol)
    if not normalized_symbol:
        unavailable["error"] = "Company symbol was missing."
        return unavailable

    clients = _get_alpaca_clients()
    if clients is None or StockBarsRequest is None or TimeFrame is None:
        unavailable["error"] = (
            f"alpaca-py is unavailable: {ALPACA_IMPORT_ERROR}"
            if ALPACA_IMPORT_ERROR is not None
            else "Alpaca credentials were not configured."
        )
        return unavailable

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=UNDERLYING_HISTORY_LOOKBACK_CALENDAR_DAYS)

    try:
        bars_response = clients["stock"].get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=normalized_symbol,
                timeframe=TimeFrame.Day,
                start=start_time,
                end=end_time,
            )
        )
        bars = _normalize_stock_bars_response(bars_response, normalized_symbol)
        return _summarize_underlying_price_history(
            normalized_symbol,
            bars,
            source="alpaca_stock_bars",
        )
    except Exception as exc:
        unavailable["error"] = str(exc)
        return unavailable


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
    expiration_date = _serialize_scalar(_get_field(contract, "expiration_date")) or ""
    days_to_expiration = _calculate_days_to_expiration(expiration_date)
    return {
        "contract_id": _serialize_scalar(_get_field(contract, "id")),
        "symbol": str(_get_field(contract, "symbol") or ""),
        "underlying_symbol": str(_get_field(contract, "underlying_symbol") or ""),
        "contract_type": _serialize_scalar(_get_field(contract, "type")) or "",
        "expiration_date": expiration_date,
        "days_to_expiration": days_to_expiration,
        "dte_bucket": _resolve_iv_percentile_dte_bucket(days_to_expiration),
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
    # Assemble the contract request so callers can work from one normalized shape.
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
    # Assemble the contract request debug so callers can work from one normalized shape.
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
    # Normalize the contract type so downstream code can rely on one consistent shape.
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
    # Handle the fetch option contracts flow in one place so callers can rely on a single, well-defined result.
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
    underlying_price_history: dict[str, Any] | None = None,
    expiration_date: str | None,
    expiration_date_gte: str | None,
    expiration_date_lte: str | None,
    strike_price_gte: float | None,
    strike_price_lte: float | None,
    contract_limit_per_type: int,
) -> dict[str, Any]:
    # Assemble the option-chain snapshot, filters, and diagnostics that the manager uses to pick contracts.
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
        "volatility_summary": summarize_option_iv(
            serialized_contracts,
            reference_stock_price,
            historical_volatility=_safe_float(
                (underlying_price_history or {}).get("historical_volatility_20d")
            ),
        ),
        "request_debug": {
            "call_request": call_request_debug,
            "put_request": put_request_debug,
        },
    }
    payload["volatility_summary"] = _enrich_with_alpaca_iv_percentiles(
        company_symbol,
        payload["volatility_summary"],
        contracts=serialized_contracts,
        reference_stock_price=reference_stock_price,
    )
    payload["volatility_summary"].update(
        {
            "historical_volatility_20d": _safe_float(
                (underlying_price_history or {}).get("historical_volatility_20d")
            ),
            "historical_volatility_60d": _safe_float(
                (underlying_price_history or {}).get("historical_volatility_60d")
            ),
            "historical_volatility_source": str(
                (underlying_price_history or {}).get("source") or ""
            ),
            "underlying_close_count": int(
                _safe_float((underlying_price_history or {}).get("close_count")) or 0
            ),
        }
    )
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
    # Build the account and position snapshot that the manager uses to reason about buying power and exposure.
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
    underlying_price_history = _build_underlying_price_history_snapshot(company_symbol)

    return {
        "current_stock_price": stock_snapshot,
        "underlying_price_history": underlying_price_history,
        "market_indices": _build_market_indices_snapshot(),
        "sector_etf": _build_sector_etf_snapshot(company),
        "option_market": _build_option_market_snapshot(
            company_symbol,
            reference_stock_price=reference_stock_price,
            underlying_price_history=underlying_price_history,
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
    # Exercise the stock-price path with one symbol so market-data configuration issues are easy to spot.
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
