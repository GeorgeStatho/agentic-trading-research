from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from services.settings import get_settings
from .common import (
    ALPACA_IMPORT_ERROR,
    DataFeed,
    LOGGER,
    StockBarsRequest,
    TimeFrame,
    _first_float,
    _get_alpaca_clients,
    _get_field,
    _serialize_scalar,
)


def _extract_bar_close(bar: Any) -> float | None:
    return _first_float(bar, "close", "c")


def _extract_bar_open(bar: Any) -> float | None:
    return _first_float(bar, "open", "o")


def _extract_bar_high(bar: Any) -> float | None:
    return _first_float(bar, "high", "h")


def _extract_bar_low(bar: Any) -> float | None:
    return _first_float(bar, "low", "l")


def _extract_bar_volume(bar: Any) -> float | None:
    return _first_float(bar, "volume", "v")


def _extract_bar_vwap(bar: Any) -> float | None:
    return _first_float(bar, "vwap", "vw")


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


def _fetch_daily_stock_bars(
    symbol: str, *, lookback_calendar_days: int | None = None,
) -> tuple[list[Any], str]:
    if lookback_calendar_days is None:
        lookback_calendar_days = get_settings().manager_context.underlying_history_lookback_days
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        return [], "Company symbol was missing."

    clients = _get_alpaca_clients()
    if clients is None or StockBarsRequest is None or TimeFrame is None or DataFeed is None:
        return [], (
            f"alpaca-py is unavailable: {ALPACA_IMPORT_ERROR}"
            if ALPACA_IMPORT_ERROR is not None
            else "Alpaca credentials were not configured."
        )

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=lookback_calendar_days)
    try:
        bars_response = clients["stock"].get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=normalized_symbol,
                timeframe=TimeFrame.Day,
                start=start_time,
                end=end_time,
                # The historical-bars endpoint otherwise defaults to SIP.  Explicit IEX
                # keeps this request compatible with Alpaca's free Basic data plan.
                feed=DataFeed.IEX,
            )
        )
        return _normalize_stock_bars_response(bars_response, normalized_symbol), ""
    except Exception as exc:
        LOGGER.debug("Failed to fetch daily stock bars for %s: %s", normalized_symbol, exc)
        return [], str(exc)
