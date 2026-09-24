"""Dated SPY/VIX history shared by the regime classifier and research database."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from db_common import DB_PATH
from db_helpers.market import load_regime_bars, store_regime_bars
from agent_helpers.market_context.common import REQUEST_HANDLER, yf
from agent_helpers.market_context.underlying_bars import (
    _extract_bar_close, _extract_bar_high, _extract_bar_low, _extract_bar_open,
    _extract_bar_timestamp, _extract_bar_volume, _fetch_daily_stock_bars,
)


LOGGER = logging.getLogger(__name__)
LOOKBACK_CALENDAR_DAYS = 550


def _normalize_bar(bar):
    try:
        timestamp = datetime.fromisoformat(str(bar["bar_at"]).replace("Z", "+00:00"))
        timestamp = timestamp.replace(tzinfo=timestamp.tzinfo or timezone.utc).astimezone(timezone.utc)
        close = float(bar["close"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(close) or close <= 0:
        return None
    return {**bar, "bar_at": timestamp.isoformat(), "close": close}


def fetch_regime_history(symbol):
    if symbol == "SPY":
        bars, error = _fetch_daily_stock_bars(symbol, lookback_calendar_days=LOOKBACK_CALENDAR_DAYS)
        if not error and len(bars) >= 252:
            return "alpaca_iex", [{
                "bar_at": _extract_bar_timestamp(bar), "close": _extract_bar_close(bar),
                "open": _extract_bar_open(bar), "high": _extract_bar_high(bar),
                "low": _extract_bar_low(bar), "volume": _extract_bar_volume(bar),
            } for bar in bars]
    if yf is None or REQUEST_HANDLER is None:
        raise RuntimeError(f"No provider available for {symbol} regime history")
    provider_symbol = "^VIX" if symbol == "VIX" else symbol
    ticker = REQUEST_HANDLER.run(yf.Ticker, provider_symbol, _context=f"Ticker({provider_symbol})")
    history = REQUEST_HANDLER.run(
        lambda: ticker.history(period="2y", interval="1d", auto_adjust=False, prepost=False),
        _context=f"history({provider_symbol},2y,1d)",
    )
    if history is None or history.empty:
        raise RuntimeError(f"No history returned for {symbol}")
    return "yfinance", [{
        "bar_at": str(timestamp), "close": row["Close"], "open": row.get("Open"),
        "high": row.get("High"), "low": row.get("Low"), "volume": row.get("Volume"),
    } for timestamp, row in history.iterrows()]


def refresh_regime_history(symbol, *, as_of=None, db_path=DB_PATH, fetcher=fetch_regime_history):
    now = as_of or datetime.now(timezone.utc)
    cached = load_regime_bars(symbol, as_of=now, db_path=db_path)
    # Refresh on each new UTC date. Failure retains timestamps on the cached bars
    # so the classifier can explicitly report stale data.
    last = datetime.fromisoformat(cached[-1]["bar_at"]) if cached else None
    if len(cached) < 252 or last.date() < now.date():
        try:
            provider, raw_bars = fetcher(symbol)
            bars = [bar for raw in raw_bars if (bar := _normalize_bar(raw)) is not None
                    and datetime.fromisoformat(bar["bar_at"]) <= now]
            store_regime_bars(symbol, bars, provider=provider, db_path=db_path)
            cached = load_regime_bars(symbol, as_of=now, db_path=db_path)
        except Exception as exc:
            LOGGER.warning("Unable to refresh %s regime history: %s", symbol, exc)
    return ([row["close_price"] for row in cached], cached[-1]["bar_at"] if cached else None)
