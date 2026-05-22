from __future__ import annotations

import math
import statistics
from typing import Any

from agent_contracts import HistoricalPricePoint, HistoricalPriceSnapshot, TrendQualityPayload

try:
    import yfinance as yf
except ImportError:
    yf = None

try:
    from yfinance_client import REQUEST_HANDLER
except ImportError:
    REQUEST_HANDLER = None


DEFAULT_TREND_QUALITY_RISK_FREE_RATE = 0.0
DEFAULT_TREND_QUALITY_TRADING_PERIODS_PER_YEAR = 252
HISTORICAL_PERIOD_CONFIG: tuple[tuple[str, str, int], ...] = (
    ("1d", "5m", 8),
    ("5d", "1h", 8),
    ("1mo", "1d", 10),
    ("3mo", "1d", 12),
)
TREND_QUALITY_WINDOW_CONFIG: tuple[tuple[str, str, str], ...] = (
    ("5d", "5d", "1h"),
    ("1m", "1mo", "1d"),
    ("3m", "3mo", "1d"),
)


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed):
        return None
    return parsed


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _serialize_timestamp(value: Any) -> str:
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return str(isoformat())
        except TypeError:
            pass
    return str(value or "")


def _empty_historical_snapshot(
    *,
    symbol: str,
    period: str,
    interval: str,
    error: str = "",
) -> HistoricalPriceSnapshot:
    return {
        "available": False,
        "symbol": symbol,
        "period": period,
        "interval": interval,
        "point_count": 0,
        "first_timestamp": "",
        "last_timestamp": "",
        "first_close": None,
        "last_close": None,
        "absolute_change": None,
        "percent_change": None,
        "period_high": None,
        "period_low": None,
        "total_volume": None,
        "recent_points": [],
        "error": error,
    }


def _empty_trend_quality_payload(*, error: str = "") -> TrendQualityPayload:
    if error:
        _ = error
    return {
        "source": "yfinance_history",
        "annualized": True,
        "risk_free_rate": DEFAULT_TREND_QUALITY_RISK_FREE_RATE,
        "trading_periods_per_year": DEFAULT_TREND_QUALITY_TRADING_PERIODS_PER_YEAR,
        "5d_sharpe": None,
        "1m_sharpe": None,
        "3m_sharpe": None,
        "5d_return_pct": None,
        "1m_return_pct": None,
        "3m_return_pct": None,
    }


def _summarize_history_frame(
    symbol: str,
    frame: Any,
    *,
    period: str,
    interval: str,
    sample_limit: int,
) -> dict[str, Any]:
    if frame is None or getattr(frame, "empty", True):
        return _empty_historical_snapshot(
            symbol=symbol,
            period=period,
            interval=interval,
            error="No yfinance history rows returned.",
        )

    sample_rows = frame.tail(max(1, int(sample_limit)))
    recent_points: list[HistoricalPricePoint] = []
    for timestamp, row in sample_rows.iterrows():
        recent_points.append(
            {
                "timestamp": _serialize_timestamp(timestamp),
                "open": _safe_float(row.get("Open")),
                "high": _safe_float(row.get("High")),
                "low": _safe_float(row.get("Low")),
                "close": _safe_float(row.get("Close")),
                "volume": _safe_int(row.get("Volume")),
            }
        )

    first_close = _safe_float(frame["Close"].iloc[0])
    last_close = _safe_float(frame["Close"].iloc[-1])
    absolute_change = None
    percent_change = None
    if first_close is not None and last_close is not None:
        absolute_change = round(last_close - first_close, 6)
        if first_close != 0:
            percent_change = round(((last_close - first_close) / first_close) * 100.0, 4)

    total_volume = None
    if "Volume" in frame:
        raw_volume = frame["Volume"].fillna(0).sum()
        total_volume = _safe_int(raw_volume)

    return {
        "available": True,
        "symbol": symbol,
        "period": period,
        "interval": interval,
        "point_count": int(len(frame.index)),
        "first_timestamp": _serialize_timestamp(frame.index[0]),
        "last_timestamp": _serialize_timestamp(frame.index[-1]),
        "first_close": first_close,
        "last_close": last_close,
        "absolute_change": absolute_change,
        "percent_change": percent_change,
        "period_high": _safe_float(frame["High"].max()) if "High" in frame else None,
        "period_low": _safe_float(frame["Low"].min()) if "Low" in frame else None,
        "total_volume": total_volume,
        "recent_points": recent_points,
        "error": "",
    }


def _build_close_returns(close_values: list[float]) -> list[float]:
    returns: list[float] = []
    previous_close: float | None = None
    for close_value in close_values:
        if close_value <= 0:
            previous_close = None
            continue
        if previous_close not in (None, 0):
            returns.append((close_value / previous_close) - 1.0)
        previous_close = close_value
    return returns


def _summarize_trend_quality_from_closes(
    close_values: list[float],
    *,
    history_period: str,
    interval: str,
) -> tuple[float | None, float | None]:
    _ = history_period
    _ = interval
    normalized_close_values = [value for value in close_values if value > 0]
    if len(normalized_close_values) < 3:
        return None, None

    close_returns = _build_close_returns(normalized_close_values)
    if len(close_returns) < 2:
        return None, None

    excess_returns = [
        close_return
        - (DEFAULT_TREND_QUALITY_RISK_FREE_RATE / float(DEFAULT_TREND_QUALITY_TRADING_PERIODS_PER_YEAR))
        for close_return in close_returns
    ]
    return_std_dev = statistics.stdev(excess_returns)
    return_pct = round(((normalized_close_values[-1] / normalized_close_values[0]) - 1.0) * 100.0, 4)
    if return_std_dev == 0:
        return None, return_pct

    annualized_sharpe = statistics.mean(excess_returns) / return_std_dev
    annualized_sharpe *= math.sqrt(DEFAULT_TREND_QUALITY_TRADING_PERIODS_PER_YEAR)
    return round(annualized_sharpe, 4), return_pct


def _extract_close_values_from_frame(frame: Any) -> list[float]:
    if frame is None or getattr(frame, "empty", True):
        return []
    if "Close" not in frame:
        return []

    close_values: list[float] = []
    for raw_close in frame["Close"].tolist():
        close_value = _safe_float(raw_close)
        if close_value is None or close_value <= 0:
            continue
        close_values.append(close_value)
    return close_values


def _summarize_trend_quality_frame(
    frame: Any,
    *,
    history_period: str,
    interval: str,
) -> tuple[float | None, float | None]:
    if frame is None or getattr(frame, "empty", True):
        return None, None
    return _summarize_trend_quality_from_closes(
        _extract_close_values_from_frame(frame),
        history_period=history_period,
        interval=interval,
    )


def _fetch_ticker_history_frame(ticker: Any, symbol: str, *, period: str, interval: str) -> Any:
    return REQUEST_HANDLER.run(
        lambda current_period=period, current_interval=interval: ticker.history(
            period=current_period,
            interval=current_interval,
            auto_adjust=False,
            prepost=False,
        ),
        _context=f"history({symbol},{period},{interval})",
    )


def _build_company_price_context(
    symbol: Any,
) -> tuple[dict[str, HistoricalPriceSnapshot], TrendQualityPayload]:
    normalized_symbol = str(symbol or "").strip().upper()
    history_by_period: dict[str, Any] = {}
    trend_quality = _empty_trend_quality_payload()

    if not normalized_symbol:
        for period, interval, _sample_limit in HISTORICAL_PERIOD_CONFIG:
            history_by_period[period] = _empty_historical_snapshot(
                symbol="",
                period=period,
                interval=interval,
                error="Company symbol was missing.",
            )
        trend_quality = _empty_trend_quality_payload(error="Company symbol was missing.")
        return history_by_period, trend_quality

    if yf is None or REQUEST_HANDLER is None:
        error = "yfinance integration is unavailable."
        for period, interval, _sample_limit in HISTORICAL_PERIOD_CONFIG:
            history_by_period[period] = _empty_historical_snapshot(
                symbol=normalized_symbol,
                period=period,
                interval=interval,
                error=error,
            )
        trend_quality = _empty_trend_quality_payload(error=error)
        return history_by_period, trend_quality

    try:
        ticker = REQUEST_HANDLER.run(
            yf.Ticker,
            normalized_symbol,
            _context=f"Ticker({normalized_symbol})",
        )
    except Exception as exc:
        error = str(exc)
        for period, interval, _sample_limit in HISTORICAL_PERIOD_CONFIG:
            history_by_period[period] = _empty_historical_snapshot(
                symbol=normalized_symbol,
                period=period,
                interval=interval,
                error=error,
            )
        trend_quality = _empty_trend_quality_payload(error=error)
        return history_by_period, trend_quality

    frame_cache: dict[tuple[str, str], Any] = {}

    def get_cached_frame(period: str, interval: str) -> Any:
        cache_key = (period, interval)
        if cache_key not in frame_cache:
            frame_cache[cache_key] = _fetch_ticker_history_frame(
                ticker,
                normalized_symbol,
                period=period,
                interval=interval,
            )
        return frame_cache[cache_key]

    for period, interval, sample_limit in HISTORICAL_PERIOD_CONFIG:
        try:
            history_frame = get_cached_frame(period, interval)
            history_by_period[period] = _summarize_history_frame(
                normalized_symbol,
                history_frame,
                period=period,
                interval=interval,
                sample_limit=sample_limit,
            )
        except Exception as exc:
            history_by_period[period] = _empty_historical_snapshot(
                symbol=normalized_symbol,
                period=period,
                interval=interval,
                error=str(exc),
            )

    trend_quality_values: dict[str, float | None] = {}
    for label, history_period, interval in TREND_QUALITY_WINDOW_CONFIG:
        try:
            trend_frame = get_cached_frame(history_period, interval)
            sharpe_value, return_pct = _summarize_trend_quality_frame(
                trend_frame,
                history_period=history_period,
                interval=interval,
            )
            trend_quality_values[f"{label}_sharpe"] = sharpe_value
            trend_quality_values[f"{label}_return_pct"] = return_pct
        except Exception:
            trend_quality_values[f"{label}_sharpe"] = None
            trend_quality_values[f"{label}_return_pct"] = None

    trend_quality = {
        **trend_quality,
        **trend_quality_values,
    }
    return history_by_period, trend_quality


def _build_company_historical_price_data(symbol: Any) -> dict[str, HistoricalPriceSnapshot]:
    historical_price_data, _trend_quality = _build_company_price_context(symbol)
    return historical_price_data
