from __future__ import annotations

import re
from typing import Any

from agent_stages.strategist_schema import (
    VALID_CONFIDENCE_LEVELS,
    VALID_DECISIONS,
    VALID_OPTION_DIRECTIONS,
    VALID_QUALITY_LEVELS,
    VALID_STOCK_DIRECTIONS,
    VALID_TIME_HORIZONS,
    VALID_TIMING_CLARITY,
)


def extract_recommendation(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    recommendation = payload.get("recommendation")
    if isinstance(recommendation, dict):
        return recommendation

    if any(
        key in payload
        for key in (
            "decision",
            "confidence",
            "evidence_quality",
            "setup_quality",
            "timing_clarity",
            "preferred_option_direction",
            "expected_stock_direction",
            "time_horizon",
            "why_now",
            "summary",
            "catalyst",
            "thesis",
            "risks",
            "contradictions_present",
            "watchlist_reason",
        )
    ):
        return payload

    for key in ("output_schema", "required_output", "result", "analysis"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            nested_recommendation = nested.get("recommendation")
            if isinstance(nested_recommendation, dict):
                return nested_recommendation
            if any(
                inner_key in nested
                for inner_key in (
                    "decision",
                    "confidence",
                    "evidence_quality",
                    "setup_quality",
                    "timing_clarity",
                    "preferred_option_direction",
                    "expected_stock_direction",
                    "time_horizon",
                    "why_now",
                    "summary",
                    "catalyst",
                    "thesis",
                    "risks",
                    "contradictions_present",
                    "watchlist_reason",
                )
            ):
                return nested

    return None


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


def _normalize_decision(value: Any) -> str:
    decision = str(value or "").strip().lower()
    replacements = {
        "buy": "trade_candidate",
        "candidate": "trade_candidate",
        "trade": "trade_candidate",
        "tradeable": "trade_candidate",
        "tradable": "trade_candidate",
        "trade_candidate": "trade_candidate",
        "trade candidate": "trade_candidate",
        "options_candidate": "trade_candidate",
        "options candidate": "trade_candidate",
        "no_buy": "do_not_trade",
        "not_buy": "do_not_trade",
        "dont_buy": "do_not_trade",
        "don't_buy": "do_not_trade",
        "do not buy": "do_not_trade",
        "not a buy": "do_not_trade",
        "avoid": "do_not_trade",
        "hold": "do_not_trade",
        "pass": "do_not_trade",
        "do_not_trade": "do_not_trade",
        "do not trade": "do_not_trade",
        "not_trade": "do_not_trade",
        "not trade": "do_not_trade",
    }
    decision = replacements.get(decision, decision)
    if decision in VALID_DECISIONS:
        return decision
    if any(token in decision for token in ("trade_candidate", "trade candidate", "tradable", "tradeable")):
        return "trade_candidate"
    if "buy" in decision and "not" not in decision and "no" not in decision:
        return "trade_candidate"
    if "trade" in decision and "not" not in decision and "no" not in decision:
        return "trade_candidate"
    if any(token in decision for token in ("not", "avoid", "hold", "pass")):
        return "do_not_trade"
    return ""


def _normalize_confidence(value: Any) -> str:
    confidence = str(value or "").strip().lower()
    replacements = {
        "strong": "high",
        "very_high": "high",
        "very high": "high",
        "moderately_high": "medium",
        "moderate": "medium",
        "uncertain": "low",
        "weak": "low",
    }
    confidence = replacements.get(confidence, confidence)
    return confidence if confidence in VALID_CONFIDENCE_LEVELS else ""


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


def _normalize_time_horizon(value: Any) -> str:
    horizon = str(value or "").strip().lower()
    replacements = {
        "1-3_days": "very_short_term",
        "1-3 days": "very_short_term",
        "very short term": "very_short_term",
        "short term": "short_term",
        "swing": "medium_term",
    }
    horizon = replacements.get(horizon, horizon)
    return horizon if horizon in VALID_TIME_HORIZONS else ""


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"true", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    return None


def _normalize_option_direction(value: Any) -> str:
    direction = str(value or "").strip().lower()
    replacements = {
        "buy_call": "call",
        "buy call": "call",
        "bullish": "call",
        "long_call": "call",
        "long call": "call",
        "calls": "call",
        "buy_put": "put",
        "buy put": "put",
        "bearish": "put",
        "long_put": "put",
        "long put": "put",
        "puts": "put",
        "none": "neither",
        "neutral": "neither",
        "no_trade": "neither",
        "no trade": "neither",
        "do_not_trade": "neither",
    }
    direction = replacements.get(direction, direction)
    return direction if direction in VALID_OPTION_DIRECTIONS else ""


def _normalize_stock_direction(value: Any) -> str:
    direction = str(value or "").strip().lower()
    replacements = {
        "bullish": "up",
        "rise": "up",
        "higher": "up",
        "upside": "up",
        "bearish": "down",
        "fall": "down",
        "lower": "down",
        "downside": "down",
        "flat": "neutral",
        "sideways": "neutral",
        "none": "neutral",
        "no_trade": "neutral",
        "no trade": "neutral",
        "do_not_trade": "neutral",
    }
    direction = replacements.get(direction, direction)
    return direction if direction in VALID_STOCK_DIRECTIONS else ""


def normalize_recommendation(recommendation: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(recommendation, dict):
        return None

    decision = _normalize_decision(recommendation.get("decision"))
    confidence = _normalize_confidence(recommendation.get("confidence"))
    evidence_quality = _normalize_quality(recommendation.get("evidence_quality"))
    setup_quality = _normalize_quality(recommendation.get("setup_quality"))
    timing_clarity = _normalize_timing_clarity(recommendation.get("timing_clarity"))
    preferred_option_direction = _normalize_option_direction(
        recommendation.get("preferred_option_direction")
        or recommendation.get("option_direction")
        or recommendation.get("options_bias")
        or recommendation.get("option_bias")
    )
    expected_stock_direction = _normalize_stock_direction(
        recommendation.get("expected_stock_direction")
        or recommendation.get("stock_direction")
        or recommendation.get("direction")
        or recommendation.get("price_direction")
    )
    time_horizon = _normalize_time_horizon(recommendation.get("time_horizon"))
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
    contradictions_present = _coerce_bool(recommendation.get("contradictions_present"))
    watchlist_reason = str(recommendation.get("watchlist_reason") or "").strip()

    if not thesis:
        thesis = _normalize_string_list(recommendation.get("bull_case"))
    if not thesis:
        thesis = _normalize_string_list(recommendation.get("bullish_points"))

    if not risks:
        risks = _normalize_string_list(recommendation.get("bear_case"))
    if not risks:
        risks = _normalize_string_list(recommendation.get("risk_factors"))

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
        direction_map = {"up": "call", "down": "put", "neutral": "neither"}
        preferred_option_direction = direction_map.get(expected_stock_direction, "")
    if not expected_stock_direction and preferred_option_direction:
        direction_map = {"call": "up", "put": "down", "neither": "neutral"}
        expected_stock_direction = direction_map.get(preferred_option_direction, "")

    if decision == "trade_candidate":
        preferred_option_direction = preferred_option_direction or "neither"
        expected_stock_direction = expected_stock_direction or "neutral"
    if decision == "watchlist":
        preferred_option_direction = preferred_option_direction or "neither"
        expected_stock_direction = expected_stock_direction or "neutral"
    if decision == "do_not_trade":
        preferred_option_direction = preferred_option_direction or "neither"
        expected_stock_direction = expected_stock_direction or "neutral"

    if decision not in VALID_DECISIONS:
        return None
    if confidence not in VALID_CONFIDENCE_LEVELS:
        return None
    if preferred_option_direction not in VALID_OPTION_DIRECTIONS:
        return None
    if expected_stock_direction not in VALID_STOCK_DIRECTIONS:
        return None
    if not summary:
        return None

    return {
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


def _extract_labeled_section(text: str, label: str, next_labels: list[str]) -> str:
    normalized_text = str(text or "").replace("**", "")
    pattern = re.compile(
        rf"{re.escape(label)}\s*:?\s*(.*?)(?=(?:{'|'.join(re.escape(item) for item in next_labels)})\s*:|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(normalized_text)
    if not match:
        return ""
    return match.group(1).strip()


def _clean_bullet_lines(text: str) -> list[str]:
    lines = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[\-\*\d\.\)\s]+", "", line).strip()
        line = line.strip("*").strip()
        if line:
            lines.append(line)
    return lines


def extract_recommendation_from_text(raw_response: str) -> dict[str, Any] | None:
    text = str(raw_response or "").strip()
    if not text:
        return None

    normalized_text = text.replace("**", "")

    decision_match = re.search(
        r"recommendation\s*:?\s*(trade[_\s-]*candidate|do[_\s-]*not[_\s-]*trade|buy|do[_\s-]*not[_\s-]*buy|hold|avoid|pass)",
        normalized_text,
        re.IGNORECASE,
    )
    option_direction_match = re.search(
        r"(preferred[_\s]?option[_\s]?direction|option[_\s]?direction|option[_\s]?bias|options[_\s]?bias)\s*:?\s*(call|put|neither|buy[_\s-]*call|buy[_\s-]*put|bullish|bearish|neutral)",
        normalized_text,
        re.IGNORECASE,
    )
    stock_direction_match = re.search(
        r"(expected[_\s]?stock[_\s]?direction|stock[_\s]?direction|direction|price[_\s]?direction)\s*:?\s*(up|down|neutral|bullish|bearish|flat|sideways)",
        normalized_text,
        re.IGNORECASE,
    )
    confidence_match = re.search(
        r"confidence\s*:?\s*(high|medium|low|strong|moderate|weak)",
        normalized_text,
        re.IGNORECASE,
    )

    summary = _extract_labeled_section(
        normalized_text,
        "Summary",
        ["Thesis", "Risks", "Risk Factors", "Bear Case", "Recommendation", "Confidence"],
    )
    thesis_text = _extract_labeled_section(
        normalized_text,
        "Thesis",
        ["Risks", "Risk Factors", "Bear Case", "Recommendation", "Confidence", "Summary"],
    )
    risks_text = _extract_labeled_section(
        normalized_text,
        "Risks",
        ["Risk Factors", "Bear Case", "Recommendation", "Confidence", "Summary", "Thesis"],
    )
    if not risks_text:
        risks_text = _extract_labeled_section(
            normalized_text,
            "Risk Factors",
            ["Bear Case", "Recommendation", "Confidence", "Summary", "Thesis"],
        )
    if not risks_text:
        risks_text = _extract_labeled_section(
            normalized_text,
            "Bear Case",
            ["Recommendation", "Confidence", "Summary", "Thesis"],
        )

    recommendation = {
        "decision": decision_match.group(1) if decision_match else "",
        "confidence": confidence_match.group(1) if confidence_match else "",
        "preferred_option_direction": option_direction_match.group(2) if option_direction_match else "",
        "expected_stock_direction": stock_direction_match.group(2) if stock_direction_match else "",
        "summary": summary,
        "thesis": _clean_bullet_lines(thesis_text),
        "risks": _clean_bullet_lines(risks_text),
    }

    return normalize_recommendation(recommendation)
