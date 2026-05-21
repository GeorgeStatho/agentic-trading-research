from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ._market_context_common import (
    ALPACA_IMPORT_ERROR,
    LOGGER,
    StockBarsRequest,
    TimeFrame,
    UNDERLYING_HISTORY_LOOKBACK_CALENDAR_DAYS,
    UNDERLYING_HV_LONG_WINDOW_DAYS,
    UNDERLYING_HV_SHORT_WINDOW_DAYS,
    _first_float,
    _get_alpaca_clients,
    _get_field,
    _safe_float,
    _serialize_scalar,
    compute_realized_volatility,
)


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
        else:
            getter = getattr(collection, "get", None)
            bars = getter(normalized_symbol) if callable(getter) else None
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
            "historical_volatility_20d": compute_realized_volatility(
                close_values,
                UNDERLYING_HV_SHORT_WINDOW_DAYS,
            ),
            "historical_volatility_60d": compute_realized_volatility(
                close_values,
                UNDERLYING_HV_LONG_WINDOW_DAYS,
            ),
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
        return _summarize_underlying_price_history(normalized_symbol, bars, source="alpaca_stock_bars")
    except Exception as exc:
        unavailable["error"] = str(exc)
        LOGGER.debug("Failed to build underlying price history for %s: %s", normalized_symbol, exc)
        return unavailable
