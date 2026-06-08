from __future__ import annotations

import re
from typing import Any

from .manager_shared import (
    DEFAULT_MANAGER_TARGET_DTE_BUCKET,
    TARGET_DTE_BUCKET_REGEX,
    TIME_HORIZON_TO_DTE_BUCKET,
    VALID_CONFIDENCE_LEVELS,
    VALID_DECISIONS,
    coerce_float,
    normalize_confidence,
    normalize_decision,
    normalize_target_dte_bucket,
    normalize_time_horizon,
)

VALID_TECHNICAL_CONFIRMATION = {"strong", "moderate", "mixed", "weak", "overextended"}
VALID_ENTRY_QUALITY = {"excellent", "good", "acceptable", "poor", "chase"}
VALID_RECOMMENDED_ACTION_ADJUSTMENT = {
    "approve",
    "approve_with_normal_size",
    "reduce_confidence",
    "reduce_size",
    "wait",
    "skip",
}
VALID_RISK_LEVELS = {"low", "medium", "high"}


def _normalize_choice(value: Any, allowed: set[str]) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else ""


def extract_recommendation(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    recommendation = payload.get("recommendation")
    if isinstance(recommendation, dict):
        return recommendation

    if any(key in payload for key in ("decision", "confidence", "target_dte_bucket", "reason")):
        return payload

    for key in ("output_schema", "required_output", "result", "analysis"):
        nested = payload.get(key)
        if not isinstance(nested, dict):
            continue

        nested_recommendation = nested.get("recommendation")
        if isinstance(nested_recommendation, dict):
            return nested_recommendation

        if any(inner_key in nested for inner_key in ("decision", "confidence", "target_dte_bucket", "reason")):
            return nested

    return None


def _map_time_horizon_to_target_dte_bucket(value: Any) -> str:
    time_horizon = normalize_time_horizon(value)
    return TIME_HORIZON_TO_DTE_BUCKET.get(time_horizon, "")


def normalize_recommendation(
    recommendation: dict[str, Any] | None,
    *,
    strategist_recommendation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(recommendation, dict):
        return None

    decision = normalize_decision(recommendation.get("decision"))
    confidence = normalize_confidence(recommendation.get("confidence"))
    target_dte_bucket = normalize_target_dte_bucket(recommendation.get("target_dte_bucket"))
    strategist_recommendation = (
        strategist_recommendation if isinstance(strategist_recommendation, dict) else {}
    )
    strategist_time_horizon_bucket = _map_time_horizon_to_target_dte_bucket(
        strategist_recommendation.get("time_horizon")
    )
    raw_reason = recommendation.get("reason") or recommendation.get("summary") or ""
    if not raw_reason and isinstance(recommendation.get("thesis"), list):
        raw_reason = "; ".join(str(x).strip() for x in recommendation["thesis"][:3] if str(x).strip())
    reason = str(raw_reason or "").strip()

    if decision not in VALID_DECISIONS:
        return None
    if confidence not in VALID_CONFIDENCE_LEVELS:
        return None
    if not target_dte_bucket:
        if decision in {"call", "put"} and strategist_time_horizon_bucket:
            target_dte_bucket = strategist_time_horizon_bucket
        elif decision in {"call", "put"}:
            target_dte_bucket = DEFAULT_MANAGER_TARGET_DTE_BUCKET
        else:
            target_dte_bucket = "none"
    if not reason:
        return None

    normalized_recommendation = {
        "decision": decision,
        "confidence": confidence,
        "target_dte_bucket": target_dte_bucket,
        "selected_option_id": None,
        "selected_expiration_date": None,
        "selected_strike_price": None,
        "selected_option_source": "manager_decision_only",
        "reason": reason,
    }
    technical_confirmation = _normalize_choice(
        recommendation.get("technical_confirmation"),
        VALID_TECHNICAL_CONFIRMATION,
    )
    entry_quality = _normalize_choice(
        recommendation.get("entry_quality"),
        VALID_ENTRY_QUALITY,
    )
    recommended_action_adjustment = _normalize_choice(
        recommendation.get("recommended_action_adjustment"),
        VALID_RECOMMENDED_ACTION_ADJUSTMENT,
    )
    overextension_risk = _normalize_choice(
        recommendation.get("overextension_risk"),
        VALID_RISK_LEVELS,
    )
    options_pricing_risk = _normalize_choice(
        recommendation.get("options_pricing_risk"),
        VALID_RISK_LEVELS,
    )
    if technical_confirmation:
        normalized_recommendation["technical_confirmation"] = technical_confirmation
    if entry_quality:
        normalized_recommendation["entry_quality"] = entry_quality
    if recommended_action_adjustment:
        normalized_recommendation["recommended_action_adjustment"] = recommended_action_adjustment
    if overextension_risk:
        normalized_recommendation["overextension_risk"] = overextension_risk
    if options_pricing_risk:
        normalized_recommendation["options_pricing_risk"] = options_pricing_risk
    for optional_text_key in ("technical_summary", "risk_summary"):
        optional_text = str(recommendation.get(optional_text_key) or "").strip()
        if optional_text:
            normalized_recommendation[optional_text_key] = optional_text
    return normalized_recommendation


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


def extract_recommendation_from_text(raw_response: str) -> dict[str, Any] | None:
    text = str(raw_response or "").strip()
    if not text:
        return None

    normalized_text = text.replace("**", "")
    decision_match = re.search(
        r"recommendation\s*:?\s*(call|put|neither|bullish|bearish|neutral|hold|avoid|pass)",
        normalized_text,
        re.IGNORECASE,
    )
    confidence_match = re.search(
        r"confidence\s*:?\s*(high|medium|low|strong|moderate|weak)",
        normalized_text,
        re.IGNORECASE,
    )

    reason = _extract_labeled_section(
        normalized_text,
        "Reason",
        ["Recommendation", "Confidence", "Summary", "Thesis", "Risks"],
    )
    if not reason:
        reason = _extract_labeled_section(
            normalized_text,
            "Summary",
            ["Recommendation", "Confidence", "Reason", "Thesis", "Risks"],
        )
    if not reason:
        reason = normalized_text

    recommendation = {
        "decision": decision_match.group(1) if decision_match else "",
        "confidence": confidence_match.group(1) if confidence_match else "",
        "target_dte_bucket": "",
        "selected_option_id": "",
        "selected_expiration_date": "",
        "selected_strike_price": "",
        "reason": reason.strip(),
    }
    target_dte_bucket_match = re.search(
        rf"(target[_\s]?dte[_\s]?bucket|dte[_\s]?bucket)\s*:?\s*({TARGET_DTE_BUCKET_REGEX})",
        normalized_text,
        re.IGNORECASE,
    )
    if target_dte_bucket_match:
        recommendation["target_dte_bucket"] = target_dte_bucket_match.group(2)
    selected_option_id_match = re.search(
        r"(selected[_\s]?option[_\s]?id|option[_\s]?id)\s*:?\s*(\d+)",
        normalized_text,
        re.IGNORECASE,
    )
    if selected_option_id_match:
        recommendation["selected_option_id"] = selected_option_id_match.group(2)

    selected_expiration_date_match = re.search(
        r"(selected[_\s]?expiration[_\s]?date|expiration[_\s]?date)\s*:?\s*(\d{4}-\d{2}-\d{2})",
        normalized_text,
        re.IGNORECASE,
    )
    if selected_expiration_date_match:
        recommendation["selected_expiration_date"] = selected_expiration_date_match.group(2)

    selected_strike_price_match = re.search(
        r"(selected[_\s]?strike[_\s]?price|strike[_\s]?price)\s*:?\s*(\d+(?:\.\d+)?)",
        normalized_text,
        re.IGNORECASE,
    )
    if selected_strike_price_match:
        recommendation["selected_strike_price"] = selected_strike_price_match.group(2)

    return recommendation
