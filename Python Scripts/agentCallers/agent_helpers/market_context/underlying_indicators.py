from __future__ import annotations

from statistics import mean
from typing import Any, Callable


RELATIVE_VOLUME_WINDOW_DAYS = 20
EMA_20_WINDOW_DAYS = 20
SMA_50_WINDOW_DAYS = 50
RSI_14_WINDOW_DAYS = 14
MACD_FAST_WINDOW_DAYS = 12
MACD_SLOW_WINDOW_DAYS = 26
MACD_SIGNAL_WINDOW_DAYS = 9
ATR_14_WINDOW_DAYS = 14
ATR_OVEREXTENSION_THRESHOLD = 2.0
RELATIVE_STRENGTH_SPY_SYMBOL = "SPY"
RELATIVE_STRENGTH_LOOKBACK_DAYS: tuple[int, ...] = (20, 50)


def _round_or_none(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None else None


def _empty_relative_strength_snapshot(*, benchmark_symbol: str, label: str) -> dict[str, Any]:
    return {
        "available": False,
        "benchmark_symbol": benchmark_symbol,
        "label": label,
        "lookback_days": list(RELATIVE_STRENGTH_LOOKBACK_DAYS),
        "price_ratio": None,
        "ratio_sma_20": None,
        "ratio_vs_sma_20_pct": None,
        "returns": {
            "20d": {
                "stock_return_pct": None,
                "benchmark_return_pct": None,
                "relative_return_pct": None,
            },
            "50d": {
                "stock_return_pct": None,
                "benchmark_return_pct": None,
                "relative_return_pct": None,
            },
        },
        "error": "",
    }


def _empty_technical_indicators_snapshot(*, sector_benchmark_symbol: str = "", sector_label: str = "") -> dict[str, Any]:
    return {
        "reference_price": None,
        "vwap": {
            "available": False,
            "value": None,
            "price_vs_vwap": None,
            "price_vs_vwap_pct": None,
        },
        "relative_volume": {
            "available": False,
            "window_days": RELATIVE_VOLUME_WINDOW_DAYS,
            "latest_volume": None,
            "average_volume_20d": None,
            "relative_volume_20d": None,
        },
        "price_vs_20ema": {
            "available": False,
            "price": None,
            "ema_20": None,
            "absolute_gap": None,
            "percent_gap": None,
        },
        "price_vs_50sma": {
            "available": False,
            "price": None,
            "sma_50": None,
            "absolute_gap": None,
            "percent_gap": None,
        },
        "rsi_14": {
            "available": False,
            "value": None,
        },
        "macd": {
            "available": False,
            "macd_line": None,
            "signal_line": None,
            "histogram": None,
        },
        "atr_overextension": {
            "available": False,
            "atr_14": None,
            "baseline_ema_20": None,
            "price_distance": None,
            "atr_multiple": None,
            "threshold_atr": ATR_OVEREXTENSION_THRESHOLD,
            "is_overextended": None,
            "direction": "",
        },
        "relative_strength": {
            "vs_spy": _empty_relative_strength_snapshot(
                benchmark_symbol=RELATIVE_STRENGTH_SPY_SYMBOL,
                label="SPY",
            ),
            "vs_sector_etf": _empty_relative_strength_snapshot(
                benchmark_symbol=sector_benchmark_symbol,
                label=sector_label or sector_benchmark_symbol,
            ),
        },
    }


def _compute_simple_moving_average(values: list[float], window_days: int) -> float | None:
    if len(values) < window_days:
        return None
    return mean(values[-window_days:])


def _compute_exponential_moving_average(values: list[float], window_days: int) -> float | None:
    if len(values) < window_days:
        return None
    multiplier = 2.0 / (window_days + 1.0)
    ema = mean(values[:window_days])
    for value in values[window_days:]:
        ema = ((value - ema) * multiplier) + ema
    return ema


def _compute_exponential_moving_average_series(values: list[float], window_days: int) -> list[float]:
    if len(values) < window_days:
        return []
    multiplier = 2.0 / (window_days + 1.0)
    ema = mean(values[:window_days])
    ema_values = [ema]
    for value in values[window_days:]:
        ema = ((value - ema) * multiplier) + ema
        ema_values.append(ema)
    return ema_values


def _compute_rsi(values: list[float], window_days: int = RSI_14_WINDOW_DAYS) -> float | None:
    if len(values) <= window_days:
        return None
    deltas = [current - previous for previous, current in zip(values, values[1:])]
    gains = [max(delta, 0.0) for delta in deltas]
    losses = [abs(min(delta, 0.0)) for delta in deltas]
    avg_gain = mean(gains[:window_days])
    avg_loss = mean(losses[:window_days])

    for gain, loss in zip(gains[window_days:], losses[window_days:]):
        avg_gain = ((avg_gain * (window_days - 1)) + gain) / window_days
        avg_loss = ((avg_loss * (window_days - 1)) + loss) / window_days

    if avg_loss == 0:
        return 100.0
    relative_strength = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _compute_macd(values: list[float]) -> tuple[float | None, float | None, float | None]:
    slow_ema_series = _compute_exponential_moving_average_series(values, MACD_SLOW_WINDOW_DAYS)
    fast_ema_series = _compute_exponential_moving_average_series(values, MACD_FAST_WINDOW_DAYS)
    if not slow_ema_series or not fast_ema_series:
        return None, None, None

    aligned_fast_ema_series = fast_ema_series[-len(slow_ema_series):]
    macd_series = [
        fast_ema - slow_ema
        for fast_ema, slow_ema in zip(aligned_fast_ema_series, slow_ema_series)
    ]
    signal_series = _compute_exponential_moving_average_series(macd_series, MACD_SIGNAL_WINDOW_DAYS)
    if not signal_series:
        return None, None, None

    macd_line = macd_series[-1]
    signal_line = signal_series[-1]
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _compute_atr(ohlc_bars: list[dict[str, float | None]], window_days: int = ATR_14_WINDOW_DAYS) -> float | None:
    if len(ohlc_bars) <= window_days:
        return None

    true_ranges: list[float] = []
    previous_close: float | None = None
    for bar in ohlc_bars:
        high = bar.get("high")
        low = bar.get("low")
        close = bar.get("close")
        if high is None or low is None or close is None:
            previous_close = close
            continue
        if previous_close is None:
            true_range = high - low
        else:
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        if true_range >= 0:
            true_ranges.append(true_range)
        previous_close = close

    if len(true_ranges) < window_days:
        return None
    return mean(true_ranges[-window_days:])


def _build_price_vs_level_snapshot(*, price: float | None, level: float | None, level_name: str) -> dict[str, Any]:
    if price is None or level is None or level == 0:
        return {
            "available": False,
            "price": _round_or_none(price),
            level_name: _round_or_none(level),
            "absolute_gap": None,
            "percent_gap": None,
        }
    absolute_gap = price - level
    percent_gap = (absolute_gap / level) * 100.0
    return {
        "available": True,
        "price": round(price, 6),
        level_name: round(level, 6),
        "absolute_gap": round(absolute_gap, 6),
        "percent_gap": round(percent_gap, 6),
    }


def _build_relative_strength_snapshot(
    stock_closes: list[float],
    benchmark_closes: list[float],
    *,
    benchmark_symbol: str,
    label: str,
    reference_price: float | None = None,
) -> dict[str, Any]:
    snapshot = _empty_relative_strength_snapshot(benchmark_symbol=benchmark_symbol, label=label)
    if len(stock_closes) < 2 or len(benchmark_closes) < 2:
        snapshot["error"] = "Insufficient close history for relative-strength comparison."
        return snapshot

    comparable_length = min(len(stock_closes), len(benchmark_closes))
    stock_series = stock_closes[-comparable_length:]
    benchmark_series = benchmark_closes[-comparable_length:]
    if not stock_series or not benchmark_series:
        snapshot["error"] = "Comparable close history was unavailable."
        return snapshot

    latest_stock_price = reference_price if reference_price is not None and reference_price > 0 else stock_series[-1]
    latest_benchmark_price = benchmark_series[-1]
    if latest_stock_price > 0 and latest_benchmark_price > 0:
        snapshot["price_ratio"] = round(latest_stock_price / latest_benchmark_price, 6)

    ratio_series = [
        stock_close / benchmark_close
        for stock_close, benchmark_close in zip(stock_series, benchmark_series)
        if benchmark_close > 0
    ]
    ratio_sma_20 = _compute_simple_moving_average(ratio_series, RELATIVE_VOLUME_WINDOW_DAYS)
    snapshot["ratio_sma_20"] = _round_or_none(ratio_sma_20)
    if snapshot["price_ratio"] is not None and ratio_sma_20 not in (None, 0):
        snapshot["ratio_vs_sma_20_pct"] = round(
            ((snapshot["price_ratio"] - ratio_sma_20) / ratio_sma_20) * 100.0,
            6,
        )

    for lookback_days in RELATIVE_STRENGTH_LOOKBACK_DAYS:
        key = f"{lookback_days}d"
        if comparable_length <= lookback_days:
            continue
        stock_base = stock_series[-(lookback_days + 1)]
        benchmark_base = benchmark_series[-(lookback_days + 1)]
        if stock_base <= 0 or benchmark_base <= 0:
            continue
        stock_return_pct = ((latest_stock_price / stock_base) - 1.0) * 100.0
        benchmark_return_pct = ((latest_benchmark_price / benchmark_base) - 1.0) * 100.0
        snapshot["returns"][key] = {
            "stock_return_pct": round(stock_return_pct, 6),
            "benchmark_return_pct": round(benchmark_return_pct, 6),
            "relative_return_pct": round(stock_return_pct - benchmark_return_pct, 6),
        }

    has_any_return = any(
        isinstance(snapshot["returns"].get(f"{lookback_days}d"), dict)
        and snapshot["returns"][f"{lookback_days}d"].get("relative_return_pct") is not None
        for lookback_days in RELATIVE_STRENGTH_LOOKBACK_DAYS
    )
    snapshot["available"] = bool(snapshot["price_ratio"] is not None or has_any_return)
    if not snapshot["available"]:
        snapshot["error"] = "Relative-strength metrics could not be computed from the available bars."
    return snapshot


def _build_technical_indicators_snapshot(
    *,
    close_values: list[float],
    volume_values: list[float],
    ohlc_bars: list[dict[str, float | None]],
    latest_vwap: float | None,
    reference_price: float | None,
    enable_relative_strength_fetch: bool = False,
    sector_benchmark_symbol: str = "",
    fetch_daily_stock_bars: Callable[[str], tuple[list[Any], str]],
    extract_bar_close: Callable[[Any], float | None],
) -> dict[str, Any]:
    sector_label = sector_benchmark_symbol or ""
    indicators = _empty_technical_indicators_snapshot(
        sector_benchmark_symbol=sector_benchmark_symbol,
        sector_label=sector_label,
    )
    latest_price = (
        reference_price
        if reference_price is not None and reference_price > 0
        else (close_values[-1] if close_values else None)
    )
    indicators["reference_price"] = _round_or_none(latest_price)

    if latest_price is not None and latest_vwap is not None and latest_vwap > 0:
        indicators["vwap"] = {
            "available": True,
            "value": round(latest_vwap, 6),
            "price_vs_vwap": round(latest_price - latest_vwap, 6),
            "price_vs_vwap_pct": round(((latest_price - latest_vwap) / latest_vwap) * 100.0, 6),
        }

    if volume_values:
        latest_volume = volume_values[-1]
        average_volume_20d = _compute_simple_moving_average(volume_values[:-1], RELATIVE_VOLUME_WINDOW_DAYS)
        relative_volume_20d = (
            latest_volume / average_volume_20d
            if average_volume_20d is not None and average_volume_20d > 0
            else None
        )
        indicators["relative_volume"] = {
            "available": relative_volume_20d is not None,
            "window_days": RELATIVE_VOLUME_WINDOW_DAYS,
            "latest_volume": round(latest_volume, 6),
            "average_volume_20d": _round_or_none(average_volume_20d),
            "relative_volume_20d": _round_or_none(relative_volume_20d),
        }

    ema_20 = _compute_exponential_moving_average(close_values, EMA_20_WINDOW_DAYS)
    indicators["price_vs_20ema"] = _build_price_vs_level_snapshot(
        price=latest_price,
        level=ema_20,
        level_name="ema_20",
    )

    sma_50 = _compute_simple_moving_average(close_values, SMA_50_WINDOW_DAYS)
    indicators["price_vs_50sma"] = _build_price_vs_level_snapshot(
        price=latest_price,
        level=sma_50,
        level_name="sma_50",
    )

    rsi_14 = _compute_rsi(close_values, RSI_14_WINDOW_DAYS)
    indicators["rsi_14"] = {
        "available": rsi_14 is not None,
        "value": _round_or_none(rsi_14),
    }

    macd_line, signal_line, histogram = _compute_macd(close_values)
    indicators["macd"] = {
        "available": macd_line is not None and signal_line is not None and histogram is not None,
        "macd_line": _round_or_none(macd_line),
        "signal_line": _round_or_none(signal_line),
        "histogram": _round_or_none(histogram),
    }

    atr_14 = _compute_atr(ohlc_bars, ATR_14_WINDOW_DAYS)
    if latest_price is not None and ema_20 is not None and atr_14 is not None and atr_14 > 0:
        price_distance = latest_price - ema_20
        atr_multiple = price_distance / atr_14
        indicators["atr_overextension"] = {
            "available": True,
            "atr_14": round(atr_14, 6),
            "baseline_ema_20": round(ema_20, 6),
            "price_distance": round(price_distance, 6),
            "atr_multiple": round(atr_multiple, 6),
            "threshold_atr": ATR_OVEREXTENSION_THRESHOLD,
            "is_overextended": abs(atr_multiple) >= ATR_OVEREXTENSION_THRESHOLD,
            "direction": "above" if atr_multiple > 0 else ("below" if atr_multiple < 0 else "flat"),
        }

    if enable_relative_strength_fetch:
        stock_closes = close_values
        spy_bars, spy_error = fetch_daily_stock_bars(RELATIVE_STRENGTH_SPY_SYMBOL)
        spy_closes = [
            close_value
            for close_value in (extract_bar_close(bar) for bar in spy_bars)
            if close_value is not None and close_value > 0
        ]
        vs_spy = _build_relative_strength_snapshot(
            stock_closes,
            spy_closes,
            benchmark_symbol=RELATIVE_STRENGTH_SPY_SYMBOL,
            label="SPY",
            reference_price=latest_price,
        )
        if not vs_spy["available"] and spy_error:
            vs_spy["error"] = spy_error
        indicators["relative_strength"]["vs_spy"] = vs_spy

        if sector_benchmark_symbol:
            sector_bars, sector_error = fetch_daily_stock_bars(sector_benchmark_symbol)
            sector_closes = [
                close_value
                for close_value in (extract_bar_close(bar) for bar in sector_bars)
                if close_value is not None and close_value > 0
            ]
            vs_sector = _build_relative_strength_snapshot(
                stock_closes,
                sector_closes,
                benchmark_symbol=sector_benchmark_symbol,
                label=sector_benchmark_symbol,
                reference_price=latest_price,
            )
            if not vs_sector["available"] and sector_error:
                vs_sector["error"] = sector_error
            indicators["relative_strength"]["vs_sector_etf"] = vs_sector

    return indicators
