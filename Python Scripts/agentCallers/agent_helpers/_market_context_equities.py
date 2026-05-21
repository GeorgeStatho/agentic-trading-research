from __future__ import annotations

import json
from typing import Any

from ._market_context_common import (
    ALPACA_IMPORT_ERROR,
    LOGGER,
    MARKET_INDEX_DEFINITIONS,
    REQUEST_HANDLER,
    SECTOR_ETF_FILE,
    StockLatestQuoteRequest,
    StockLatestTradeRequest,
    _first_float,
    _get_alpaca_clients,
    _get_field,
    _normalize_identifier,
    _safe_float,
    _serialize_scalar,
    yf,
)

_SECTOR_ETF_MAP: dict[str, dict[str, Any]] | None = None


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
        ticker = REQUEST_HANDLER.run(yf.Ticker, symbol, _context=f"Ticker({symbol})")
        fast_info = REQUEST_HANDLER.run(lambda: ticker.fast_info, _context=f"fast_info({symbol})")
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
                price = _safe_float(history["Close"].iloc[-1])
                if previous_close is None and len(history.index) >= 2:
                    previous_close = _safe_float(history["Close"].iloc[-2])
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

    _SECTOR_ETF_MAP = {
        _normalize_identifier(raw_key): raw_value
        for raw_key, raw_value in raw_payload.items()
        if _normalize_identifier(raw_key) and isinstance(raw_value, dict)
    }
    return _SECTOR_ETF_MAP


def _resolve_company_sector_etf(company: dict[str, Any]) -> tuple[str, dict[str, Any], str] | None:
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
        if sector_name == _normalize_identifier(etf_entry.get("name")):
            return etf_key, etf_entry, "sector_name"
    return None


def _build_sector_etf_snapshot(company: dict[str, Any]) -> dict[str, Any]:
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
    snapshot = _build_market_index_snapshot(
        symbol=ticker,
        label=str(etf_entry.get("fund_name") or etf_entry.get("name") or "").strip(),
    )
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
                    latest_trade_timestamp = _serialize_scalar(_get_field(trade, "timestamp")) or ""
            except Exception:
                latest_trade_price = None
                latest_trade_timestamp = ""

        bid_price = _safe_float(_get_field(quote, "bid_price"))
        ask_price = _safe_float(_get_field(quote, "ask_price"))
        midpoint_price = None
        if bid_price is not None and ask_price is not None:
            midpoint_price = round((bid_price + ask_price) / 2.0, 4)

        price = latest_trade_price or ask_price or bid_price or midpoint_price
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
