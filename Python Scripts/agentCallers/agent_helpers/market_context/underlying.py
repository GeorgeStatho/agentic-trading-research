from __future__ import annotations

from typing import Any

from services.core.settings import get_settings
from agent_helpers.volatility import compute_realized_volatility
from .underlying_bars import (
    _extract_bar_close as _extract_bar_close_impl,
    _extract_bar_high as _extract_bar_high_impl,
    _extract_bar_low as _extract_bar_low_impl,
    _extract_bar_open as _extract_bar_open_impl,
    _extract_bar_timestamp as _extract_bar_timestamp_impl,
    _extract_bar_volume as _extract_bar_volume_impl,
    _extract_bar_vwap as _extract_bar_vwap_impl,
    _fetch_daily_stock_bars as _fetch_daily_stock_bars_impl,
)
from .underlying_indicators import _build_technical_indicators_snapshot, _empty_technical_indicators_snapshot
from .underlying_technical_summary import build_technical_summary


def _empty_underlying_price_history_snapshot(*, symbol: str, error: str = "") -> dict[str, Any]:
    return {
        "available": False,
        "symbol": symbol,
        "source": "alpaca_stock_bars",
        "feed": "iex",
        "timeframe": "1Day",
        "lookback_calendar_days": get_settings().manager_context.underlying_history_lookback_days,
        "close_count": 0,
        "first_bar_timestamp": "",
        "last_bar_timestamp": "",
        "first_close": None,
        "last_close": None,
        "historical_volatility_20d": None,
        "historical_volatility_60d": None,
        "recent_closes": [],
        "technical_indicators": _empty_technical_indicators_snapshot(),
        "technical_summary": build_technical_summary(None),
        "error": error,
    }


def _extract_bar_close(bar: Any) -> float | None:
    return _extract_bar_close_impl(bar)


def _extract_bar_open(bar: Any) -> float | None:
    return _extract_bar_open_impl(bar)


def _extract_bar_high(bar: Any) -> float | None:
    return _extract_bar_high_impl(bar)


def _extract_bar_low(bar: Any) -> float | None:
    return _extract_bar_low_impl(bar)


def _extract_bar_volume(bar: Any) -> float | None:
    return _extract_bar_volume_impl(bar)


def _extract_bar_vwap(bar: Any) -> float | None:
    return _extract_bar_vwap_impl(bar)


def _extract_bar_timestamp(bar: Any) -> str:
    return _extract_bar_timestamp_impl(bar)


def _fetch_daily_stock_bars(symbol: str) -> tuple[list[Any], str]:
    return _fetch_daily_stock_bars_impl(symbol)


def _summarize_underlying_price_history(
    symbol: str,
    bars: list[Any],
    *,
    source: str = "alpaca_stock_bars",
    reference_price: float | None = None,
    sector_benchmark_symbol: str = "",
    enable_relative_strength_fetch: bool = False,
) -> dict[str, Any]:
    snapshot = _empty_underlying_price_history_snapshot(symbol=symbol)
    snapshot["technical_indicators"] = _empty_technical_indicators_snapshot(
        sector_benchmark_symbol=sector_benchmark_symbol,
        sector_label=sector_benchmark_symbol,
    )
    snapshot["technical_summary"] = build_technical_summary(None)
    normalized_symbol = str(symbol or "").strip().upper()
    close_values: list[float] = []
    volume_values: list[float] = []
    timestamp_values: list[str] = []
    ohlc_bars: list[dict[str, float | None]] = []
    latest_vwap: float | None = None
    for bar in bars:
        close_price = _extract_bar_close(bar)
        timestamp = _extract_bar_timestamp(bar)
        if close_price is None or close_price <= 0:
            continue
        close_values.append(close_price)
        open_price = _extract_bar_open(bar)
        high_price = _extract_bar_high(bar)
        low_price = _extract_bar_low(bar)
        volume = _extract_bar_volume(bar)
        if volume is not None and volume >= 0:
            volume_values.append(volume)
        vwap = _extract_bar_vwap(bar)
        if vwap is not None and vwap > 0:
            latest_vwap = vwap
        ohlc_bars.append(
            {
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
            }
        )
        if timestamp:
            timestamp_values.append(timestamp)

    if not close_values:
        snapshot["error"] = "No valid close prices were returned from Alpaca stock bars."
        return snapshot

    technical_indicators = _build_technical_indicators_snapshot(
        close_values=close_values,
        volume_values=volume_values,
        ohlc_bars=ohlc_bars,
        latest_vwap=latest_vwap,
        reference_price=reference_price,
        enable_relative_strength_fetch=enable_relative_strength_fetch,
        sector_benchmark_symbol=sector_benchmark_symbol,
        fetch_daily_stock_bars=_fetch_daily_stock_bars,
        extract_bar_close=_extract_bar_close,
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
            "historical_volatility_20d": compute_realized_volatility(
                close_values,
                get_settings().manager_context.hv_short_window_days,
            ),
            "historical_volatility_60d": compute_realized_volatility(
                close_values,
                get_settings().manager_context.hv_long_window_days,
            ),
            "recent_closes": [round(value, 6) for value in close_values[-5:]],
            "technical_indicators": technical_indicators,
            "technical_summary": build_technical_summary(technical_indicators),
            "error": "",
        }
    )
    return snapshot


def _build_underlying_price_history_snapshot(
    symbol: str,
    *,
    reference_price: float | None = None,
    sector_benchmark_symbol: str = "",
) -> dict[str, Any]:
    normalized_symbol = str(symbol or "").strip().upper()
    unavailable = _empty_underlying_price_history_snapshot(symbol=normalized_symbol)
    unavailable["technical_indicators"] = _empty_technical_indicators_snapshot(
        sector_benchmark_symbol=sector_benchmark_symbol,
        sector_label=sector_benchmark_symbol,
    )
    if not normalized_symbol:
        unavailable["error"] = "Company symbol was missing."
        return unavailable

    bars, error = _fetch_daily_stock_bars(normalized_symbol)
    if error:
        unavailable["error"] = error
        return unavailable
    return _summarize_underlying_price_history(
        normalized_symbol,
        bars,
        source="alpaca_stock_bars",
        reference_price=reference_price,
        sector_benchmark_symbol=sector_benchmark_symbol,
        enable_relative_strength_fetch=True,
    )
