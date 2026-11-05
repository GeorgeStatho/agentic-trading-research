from __future__ import annotations

from typing import Any
from services.core.numbers import safe_float


def _empty_technical_summary_snapshot() -> dict[str, Any]:
    return {
        "available": False,
        "trend_state": "unknown",
        "momentum_state": "unknown",
        "vwap_state": "unknown",
        "volume_confirmation": "unknown",
        "relative_strength_confirmation": "unknown",
        "overextension_risk": "unknown",
        "call_entry_quality": "unknown",
        "put_entry_quality": "unknown",
        "technical_confirmation_for_calls": "unknown",
        "technical_confirmation_for_puts": "unknown",
        "recommended_llm_adjustment": "reduce_confidence",
        "warnings": [],
    }


def _resolve_trend_state(indicators: dict[str, Any]) -> str:
    ema_gap = safe_float(((indicators.get("price_vs_20ema") or {}).get("percent_gap")))
    sma_gap = safe_float(((indicators.get("price_vs_50sma") or {}).get("percent_gap")))
    if ema_gap is None or sma_gap is None:
        return "unknown"
    if ema_gap > 0 and sma_gap > 0:
        return "bullish"
    if ema_gap < 0 and sma_gap < 0:
        return "bearish"
    return "mixed"


def _resolve_momentum_state(indicators: dict[str, Any]) -> str:
    rsi = safe_float(((indicators.get("rsi_14") or {}).get("value")))
    macd_histogram = safe_float(((indicators.get("macd") or {}).get("histogram")))
    if rsi is None or macd_histogram is None:
        return "unknown"
    if 50 <= rsi <= 70 and macd_histogram > 0:
        return "positive_not_overheated"
    if 70 < rsi < 78 and macd_histogram > 0:
        return "hot"
    if rsi >= 78:
        return "overheated"
    if rsi < 45 and macd_histogram < 0:
        return "weak"
    return "mixed"


def _resolve_vwap_state(indicators: dict[str, Any], warnings: list[str]) -> str:
    vwap_block = indicators.get("vwap") or {}
    if not vwap_block.get("available"):
        return "unknown"
    warnings.append("VWAP is daily-bar VWAP, not true intraday VWAP")
    gap_pct = safe_float(vwap_block.get("price_vs_vwap_pct"))
    if gap_pct is None:
        return "unknown"
    if gap_pct > 0.25:
        return "above_vwap"
    if gap_pct < -0.25:
        return "below_vwap"
    return "near_vwap"


def _resolve_volume_confirmation(indicators: dict[str, Any]) -> str:
    relative_volume = safe_float(((indicators.get("relative_volume") or {}).get("relative_volume_20d")))
    if relative_volume is None:
        return "unknown"
    if relative_volume >= 1.5:
        return "strong"
    if relative_volume >= 1.0:
        return "normal"
    return "weak"


def _resolve_relative_strength_confirmation(indicators: dict[str, Any]) -> str:
    relative_strength = indicators.get("relative_strength") or {}
    spy_relative = safe_float(
        (((relative_strength.get("vs_spy") or {}).get("returns") or {}).get("20d") or {}).get("relative_return_pct")
    )
    sector_relative = safe_float(
        (((relative_strength.get("vs_sector_etf") or {}).get("returns") or {}).get("20d") or {}).get("relative_return_pct")
    )
    available_values = [value for value in (spy_relative, sector_relative) if value is not None]
    if not available_values:
        return "unknown"
    if spy_relative is not None and sector_relative is not None:
        if spy_relative > 0 and sector_relative > 0:
            return "strong"
        if spy_relative > 0 or sector_relative > 0:
            return "mixed_positive"
        return "weak"
    return "mixed_positive" if any(value > 0 for value in available_values) else "weak"


def _resolve_overextension_risk(indicators: dict[str, Any]) -> str:
    atr_multiple = safe_float(((indicators.get("atr_overextension") or {}).get("atr_multiple")))
    rsi = safe_float(((indicators.get("rsi_14") or {}).get("value")))
    if atr_multiple is None:
        return "unknown"
    absolute_atr_multiple = abs(atr_multiple)
    if absolute_atr_multiple >= 2.0:
        risk = "high"
    elif absolute_atr_multiple >= 1.25:
        risk = "medium"
    else:
        risk = "low"
    if rsi is not None and rsi >= 78 and atr_multiple > 1.25:
        risk = "high"
    return risk


def _score_call_entry_quality(
    *,
    trend_state: str,
    momentum_state: str,
    vwap_state: str,
    volume_confirmation: str,
    relative_strength_confirmation: str,
    overextension_risk: str,
) -> tuple[str, int]:
    score = 0
    if trend_state == "bullish":
        score += 2
    elif trend_state == "bearish":
        score -= 3

    if momentum_state == "positive_not_overheated":
        score += 2
    elif momentum_state == "weak":
        score -= 2
    elif momentum_state == "overheated":
        score -= 2

    if vwap_state == "above_vwap":
        score += 1
    elif vwap_state == "below_vwap":
        score -= 2

    if volume_confirmation == "strong":
        score += 2
    elif volume_confirmation == "weak":
        score -= 2

    if relative_strength_confirmation == "strong":
        score += 2
    elif relative_strength_confirmation == "weak":
        score -= 2

    if overextension_risk == "low":
        score += 2
    elif overextension_risk == "medium":
        score += 1
    elif overextension_risk == "high":
        score -= 3

    if overextension_risk == "high" and momentum_state in {"hot", "overheated"}:
        return "chase_risk", score
    if score >= 8:
        return "excellent", score
    if score >= 6:
        return "good", score
    if score >= 3:
        return "acceptable", score
    return "poor", score


def _score_put_entry_quality(
    *,
    trend_state: str,
    momentum_state: str,
    vwap_state: str,
    volume_confirmation: str,
    relative_strength_confirmation: str,
    overextension_risk: str,
) -> str:
    score = 0
    if trend_state == "bearish":
        score += 2
    elif trend_state == "bullish":
        score -= 3

    if momentum_state == "weak":
        score += 2
    elif momentum_state in {"hot", "overheated", "positive_not_overheated"}:
        score -= 2

    if vwap_state == "below_vwap":
        score += 1
    elif vwap_state == "above_vwap":
        score -= 2

    if volume_confirmation == "strong":
        score += 1
    elif volume_confirmation == "weak":
        score -= 1

    if relative_strength_confirmation == "weak":
        score += 2
    elif relative_strength_confirmation in {"strong", "mixed_positive"}:
        score -= 2

    if overextension_risk == "high" and trend_state == "bearish":
        score -= 1

    if score >= 7:
        return "excellent"
    if score >= 5:
        return "good"
    if score >= 2:
        return "acceptable"
    return "poor"


def _resolve_technical_confirmation_for_calls(
    *,
    trend_state: str,
    momentum_state: str,
    volume_confirmation: str,
    relative_strength_confirmation: str,
    overextension_risk: str,
) -> str:
    if (
        trend_state == "bullish"
        and momentum_state == "positive_not_overheated"
        and volume_confirmation == "strong"
        and relative_strength_confirmation == "strong"
        and overextension_risk in {"low", "medium"}
    ):
        return "strong"
    if trend_state == "bearish" and momentum_state == "weak" and relative_strength_confirmation == "weak":
        return "bearish"
    bullish_signals = sum(
        [
            trend_state == "bullish",
            momentum_state in {"positive_not_overheated", "hot"},
            volume_confirmation in {"strong", "normal"},
            relative_strength_confirmation in {"strong", "mixed_positive"},
        ]
    )
    caution_signals = sum(
        [
            overextension_risk == "high",
            momentum_state in {"weak", "overheated"},
            trend_state == "bearish",
            relative_strength_confirmation == "weak",
        ]
    )
    if bullish_signals >= 3 and caution_signals <= 1:
        return "moderate"
    if bullish_signals >= 2 and caution_signals <= 2:
        return "mixed"
    if bullish_signals == 0 and caution_signals >= 2:
        return "bearish"
    return "weak"


def _resolve_technical_confirmation_for_puts(
    *,
    trend_state: str,
    momentum_state: str,
    volume_confirmation: str,
    relative_strength_confirmation: str,
) -> str:
    bearish_signals = sum(
        [
            trend_state == "bearish",
            momentum_state == "weak",
            volume_confirmation in {"strong", "normal"},
            relative_strength_confirmation == "weak",
        ]
    )
    bullish_signals = sum(
        [
            trend_state == "bullish",
            momentum_state in {"positive_not_overheated", "hot", "overheated"},
            relative_strength_confirmation in {"strong", "mixed_positive"},
        ]
    )
    if bearish_signals >= 4:
        return "strong"
    if bearish_signals >= 3:
        return "moderate"
    if bullish_signals >= 2:
        return "weak"
    return "mixed"


def _resolve_recommended_llm_adjustment(
    *,
    technical_confirmation_for_calls: str,
    call_entry_quality: str,
) -> str:
    if technical_confirmation_for_calls == "strong" and call_entry_quality in {"excellent", "good"}:
        return "approve_if_news_and_options_confirm"
    if call_entry_quality == "acceptable":
        return "approve_if_news_and_options_confirm"
    if call_entry_quality == "chase_risk":
        return "wait_for_better_entry"
    if technical_confirmation_for_calls == "weak":
        return "reduce_confidence"
    if technical_confirmation_for_calls == "bearish":
        return "skip_unless_exceptional_catalyst"
    return "reduce_size"


def build_technical_summary(indicators: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(indicators, dict):
        return _empty_technical_summary_snapshot()

    warnings: list[str] = []
    trend_state = _resolve_trend_state(indicators)
    momentum_state = _resolve_momentum_state(indicators)
    vwap_state = _resolve_vwap_state(indicators, warnings)
    volume_confirmation = _resolve_volume_confirmation(indicators)
    relative_strength_confirmation = _resolve_relative_strength_confirmation(indicators)
    overextension_risk = _resolve_overextension_risk(indicators)
    call_entry_quality, _ = _score_call_entry_quality(
        trend_state=trend_state,
        momentum_state=momentum_state,
        vwap_state=vwap_state,
        volume_confirmation=volume_confirmation,
        relative_strength_confirmation=relative_strength_confirmation,
        overextension_risk=overextension_risk,
    )
    put_entry_quality = _score_put_entry_quality(
        trend_state=trend_state,
        momentum_state=momentum_state,
        vwap_state=vwap_state,
        volume_confirmation=volume_confirmation,
        relative_strength_confirmation=relative_strength_confirmation,
        overextension_risk=overextension_risk,
    )
    technical_confirmation_for_calls = _resolve_technical_confirmation_for_calls(
        trend_state=trend_state,
        momentum_state=momentum_state,
        volume_confirmation=volume_confirmation,
        relative_strength_confirmation=relative_strength_confirmation,
        overextension_risk=overextension_risk,
    )
    technical_confirmation_for_puts = _resolve_technical_confirmation_for_puts(
        trend_state=trend_state,
        momentum_state=momentum_state,
        volume_confirmation=volume_confirmation,
        relative_strength_confirmation=relative_strength_confirmation,
    )
    recommended_llm_adjustment = _resolve_recommended_llm_adjustment(
        technical_confirmation_for_calls=technical_confirmation_for_calls,
        call_entry_quality=call_entry_quality,
    )

    summary = {
        "available": any(
            state != "unknown"
            for state in (
                trend_state,
                momentum_state,
                vwap_state,
                volume_confirmation,
                relative_strength_confirmation,
                overextension_risk,
            )
        ),
        "trend_state": trend_state,
        "momentum_state": momentum_state,
        "vwap_state": vwap_state,
        "volume_confirmation": volume_confirmation,
        "relative_strength_confirmation": relative_strength_confirmation,
        "overextension_risk": overextension_risk,
        "call_entry_quality": call_entry_quality,
        "put_entry_quality": put_entry_quality,
        "technical_confirmation_for_calls": technical_confirmation_for_calls,
        "technical_confirmation_for_puts": technical_confirmation_for_puts,
        "recommended_llm_adjustment": recommended_llm_adjustment,
        "warnings": warnings,
    }
    if not summary["available"]:
        return _empty_technical_summary_snapshot()
    return summary
