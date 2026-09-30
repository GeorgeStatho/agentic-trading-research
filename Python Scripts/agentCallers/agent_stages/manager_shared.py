from __future__ import annotations

import logging
import os
import re
from typing import Any

from agent_helpers.shared import Client, ask_llm_model, get_model_client
from services.option_dte_buckets import (
    DEFAULT_MANAGER_TARGET_DTE_BUCKET,
    ORDERED_OPTION_DTE_BUCKET_KEYS_WITH_NONE,
    TIME_HORIZON_TO_DTE_BUCKET,
    VALID_OPTION_DTE_BUCKET_KEYS_WITH_NONE,
    get_all_target_dte_bucket_aliases,
    get_target_dte_bucket_choices_text,
    get_time_horizon_mapping_text,
    normalize_target_dte_bucket,
    target_dte_bucket_to_expiration_filters,
)


MODEL_BACKEND_LABEL = os.getenv(
    "MANAGER_OLLAMA_HOST",
    os.getenv("MACRO_NEWS_OLLAMA_HOST", os.getenv("WORLD_NEWS_OLLAMA_HOST", "http://127.0.0.1:11434")),
)
DEFAULT_MODEL = os.getenv(
    "MANAGER_MODEL",
    os.getenv("MACRO_NEWS_MODEL", os.getenv("WORLD_NEWS_MODEL", "world-news-sectors")),
)
MANAGER_STAGE_VERSION = "decision-only-v3"
MANAGER_PROMPT_VERSION = "manager-prompt-v4-evidence-thesis"

VALID_DECISIONS = {"call", "put", "neither"}
VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}
VALID_TARGET_DTE_BUCKETS = VALID_OPTION_DTE_BUCKET_KEYS_WITH_NONE
VALID_STRATEGIST_DECISIONS = {"trade_candidate", "watchlist", "do_not_trade"}
VALID_QUALITY_LEVELS = {"strong", "moderate", "weak"}
VALID_TIMING_CLARITY = {"clear", "unclear"}
VALID_TIME_HORIZONS = {*TIME_HORIZON_TO_DTE_BUCKET.keys(), "unclear"}
MANAGER_REASON_MAX_CHARS = 600
MANAGER_SUMMARY_MAX_CHARS = 600

LOGGER = logging.getLogger(__name__)
TARGET_DTE_BUCKET_CHOICES_TEXT = get_target_dte_bucket_choices_text()
TIME_HORIZON_DTE_MAPPING_TEXT = get_time_horizon_mapping_text()
TARGET_DTE_BUCKET_REGEX = "|".join(
    sorted(
        (re.escape(alias) for alias in get_all_target_dte_bucket_aliases()),
        key=len,
        reverse=True,
    )
)
MANAGER_RECOMMENDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recommendation": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["call", "put", "neither"]},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                # Requested by the prompt; the closed schema previously made them unreachable.
                "thesis_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "timing_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "execution_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "target_dte_bucket": {"type": "string", "enum": list(ORDERED_OPTION_DTE_BUCKET_KEYS_WITH_NONE)},
                "technical_confirmation": {"type": "string", "enum": ["strong", "moderate", "mixed", "weak", "overextended"]},
                "entry_quality": {"type": "string", "enum": ["excellent", "good", "acceptable", "poor", "chase"]},
                "recommended_action_adjustment": {"type": "string", "enum": ["approve", "approve_with_normal_size", "reduce_confidence", "reduce_size", "wait", "skip"]},
                "overextension_risk": {"type": "string", "enum": ["low", "medium", "high"]},
                "options_pricing_risk": {"type": "string", "enum": ["low", "medium", "high"]},
                "technical_summary": {"type": "string", "maxLength": MANAGER_SUMMARY_MAX_CHARS},
                "risk_summary": {"type": "string", "maxLength": MANAGER_SUMMARY_MAX_CHARS},
                "reason": {"type": "string", "maxLength": MANAGER_REASON_MAX_CHARS},
            },
            "required": ["decision", "confidence", "target_dte_bucket", "technical_confirmation", "entry_quality", "recommended_action_adjustment", "reason"],
            "additionalProperties": False,
        }
    },
    "required": ["recommendation"],
    "additionalProperties": False,
}

_manager_client: Client | None = None


def get_default_client() -> Client:
    global _manager_client
    if _manager_client is None:
        _manager_client = get_model_client(MODEL_BACKEND_LABEL)
    return _manager_client


def ask_model(
    client: Client,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    response_diagnostic: dict[str, Any] | None = None,
) -> str:
    return ask_llm_model(
        client,
        model,
        system_prompt,
        user_prompt,
        temperature=0,
        host_label=MODEL_BACKEND_LABEL,
        response_schema=MANAGER_RECOMMENDATION_SCHEMA,
        response_diagnostic=response_diagnostic,
    )


def normalize_decision(value: Any) -> str:
    decision = str(value or "").strip().lower()
    replacements = {
        "bullish": "call",
        "buy_call": "call",
        "buy call": "call",
        "calls": "call",
        "bearish": "put",
        "buy_put": "put",
        "buy put": "put",
        "puts": "put",
        "hold": "neither",
        "avoid": "neither",
        "pass": "neither",
        "no_trade": "neither",
        "no trade": "neither",
        "do nothing": "neither",
        "neutral": "neither",
    }
    decision = replacements.get(decision, decision)
    return decision if decision in VALID_DECISIONS else ""


def normalize_confidence(value: Any) -> str:
    confidence = str(value or "").strip().lower()
    replacements = {
        "strong": "high",
        "very_high": "high",
        "very high": "high",
        "moderate": "medium",
        "uncertain": "low",
        "weak": "low",
    }
    confidence = replacements.get(confidence, confidence)
    return confidence if confidence in VALID_CONFIDENCE_LEVELS else ""


def normalize_time_horizon(value: Any) -> str:
    horizon = str(value or "").strip().lower()
    replacements = {
        "1-3_days": "very_short_term",
        "1-3 days": "very_short_term",
        "swing": "medium_term",
    }
    for valid_horizon in TIME_HORIZON_TO_DTE_BUCKET:
        replacements.setdefault(valid_horizon, valid_horizon)
        replacements.setdefault(valid_horizon.replace("_", " "), valid_horizon)
    horizon = replacements.get(horizon, horizon)
    return horizon if horizon in VALID_TIME_HORIZONS else "unclear"


def coerce_bool(value: Any) -> bool | None:
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


def normalize_option_id(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None

    text = str(value or "").strip()
    if not text:
        return None
    try:
        normalized = int(text)
    except ValueError:
        return None
    return normalized if normalized > 0 else None


def coerce_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
