from __future__ import annotations

from typing import Any

from .manager_shared import (
    VALID_QUALITY_LEVELS,
    VALID_STRATEGIST_DECISIONS,
    VALID_TIMING_CLARITY,
    coerce_bool,
    normalize_confidence,
    normalize_decision,
    normalize_time_horizon,
)


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            cleaned.append(text)
    return cleaned


def _normalize_strategist_decision(value: Any) -> str:
    decision = str(value or "").strip().lower()
    replacements = {
        "buy": "trade_candidate",
        "candidate": "trade_candidate",
        "trade": "trade_candidate",
        "trade candidate": "trade_candidate",
        "trade_candidate": "trade_candidate",
        "watch": "watchlist",
        "watch list": "watchlist",
        "watchlist": "watchlist",
        "hold_for_now": "watchlist",
        "hold for now": "watchlist",
        "do_not_trade": "do_not_trade",
        "do not trade": "do_not_trade",
        "avoid": "do_not_trade",
        "pass": "do_not_trade",
        "hold": "do_not_trade",
    }
    decision = replacements.get(decision, decision)
    return decision if decision in VALID_STRATEGIST_DECISIONS else ""


def _normalize_quality(value: Any) -> str:
    quality = str(value or "").strip().lower()
    replacements = {
        "high": "strong",
        "medium": "moderate",
        "low": "weak",
    }
    quality = replacements.get(quality, quality)
    return quality if quality in VALID_QUALITY_LEVELS else ""


def _normalize_timing_clarity(value: Any) -> str:
    timing = str(value or "").strip().lower()
    replacements = {
        "timely": "clear",
        "actionable": "clear",
        "good": "clear",
        "bad": "unclear",
        "mixed": "unclear",
    }
    timing = replacements.get(timing, timing)
    return timing if timing in VALID_TIMING_CLARITY else ""


def _normalize_strategist_recommendation(recommendation: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(recommendation, dict):
        return {}

    decision = _normalize_strategist_decision(recommendation.get("decision"))
    confidence = normalize_confidence(recommendation.get("confidence"))
    evidence_quality = _normalize_quality(recommendation.get("evidence_quality"))
    setup_quality = _normalize_quality(recommendation.get("setup_quality"))
    timing_clarity = _normalize_timing_clarity(recommendation.get("timing_clarity"))
    preferred_option_direction = normalize_decision(
        recommendation.get("preferred_option_direction")
        or recommendation.get("option_direction")
        or recommendation.get("options_bias")
        or recommendation.get("option_bias")
    )
    expected_stock_direction = str(
        recommendation.get("expected_stock_direction")
        or recommendation.get("stock_direction")
        or recommendation.get("direction")
        or recommendation.get("price_direction")
        or ""
    ).strip().lower()
    stock_direction_replacements = {
        "bullish": "up",
        "bearish": "down",
        "flat": "neutral",
        "sideways": "neutral",
    }
    expected_stock_direction = stock_direction_replacements.get(
        expected_stock_direction,
        expected_stock_direction,
    )
    if expected_stock_direction not in {"up", "down", "neutral"}:
        expected_stock_direction = ""

    time_horizon = normalize_time_horizon(recommendation.get("time_horizon"))
    why_now = str(
        recommendation.get("why_now")
        or recommendation.get("why_now_summary")
        or recommendation.get("timing_reason")
        or ""
    ).strip()
    summary = str(recommendation.get("summary") or recommendation.get("reason") or "").strip()
    catalyst = _normalize_string_list(recommendation.get("catalyst"))
    thesis = _normalize_string_list(recommendation.get("thesis"))
    risks = _normalize_string_list(recommendation.get("risks"))
    contradictions_present = coerce_bool(recommendation.get("contradictions_present"))
    watchlist_reason = str(recommendation.get("watchlist_reason") or "").strip()

    if not summary and thesis:
        summary = thesis[0]
    if not why_now:
        why_now = summary or watchlist_reason
    if not evidence_quality and confidence:
        evidence_quality = {"high": "strong", "medium": "moderate", "low": "weak"}.get(confidence, "")
    if not setup_quality:
        if decision == "trade_candidate":
            setup_quality = {"high": "strong", "medium": "moderate", "low": "weak"}.get(confidence, "")
        elif decision in {"watchlist", "do_not_trade"}:
            setup_quality = "weak" if confidence == "low" else "moderate"
    if not timing_clarity:
        timing_clarity = "clear" if decision == "trade_candidate" else "unclear"
    if not time_horizon:
        time_horizon = "unclear"
    if contradictions_present is None:
        contradictions_present = False
    if decision == "watchlist" and not watchlist_reason:
        watchlist_reason = why_now or summary

    if not preferred_option_direction and expected_stock_direction:
        preferred_option_direction = {"up": "call", "down": "put", "neutral": "neither"}.get(
            expected_stock_direction,
            "",
        )
    if not expected_stock_direction and preferred_option_direction:
        expected_stock_direction = {"call": "up", "put": "down", "neither": "neutral"}.get(
            preferred_option_direction,
            "",
        )

    if decision == "do_not_trade":
        preferred_option_direction = preferred_option_direction or "neither"
        expected_stock_direction = expected_stock_direction or "neutral"
    if decision == "watchlist" and not preferred_option_direction:
        preferred_option_direction = "neither"
    if decision == "watchlist" and not expected_stock_direction:
        expected_stock_direction = "neutral"

    normalized = {
        "decision": decision,
        "confidence": confidence,
        "evidence_quality": evidence_quality,
        "setup_quality": setup_quality,
        "timing_clarity": timing_clarity,
        "preferred_option_direction": preferred_option_direction,
        "expected_stock_direction": expected_stock_direction,
        "time_horizon": time_horizon,
        "why_now": why_now,
        "summary": summary,
        "catalyst": catalyst,
        "thesis": thesis,
        "risks": risks,
        "contradictions_present": contradictions_present,
        "watchlist_reason": watchlist_reason,
    }
    return {key: value for key, value in normalized.items() if value not in ("", [], None)}


def _build_manager_visible_strategist_context(payload: dict[str, Any]) -> dict[str, Any]:
    return _normalize_strategist_recommendation(payload.get("strategist_recommendation", {}))
