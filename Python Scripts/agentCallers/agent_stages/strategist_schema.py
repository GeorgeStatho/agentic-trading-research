from __future__ import annotations

from typing import Any

from services.option_dte_buckets import TIME_HORIZON_TO_DTE_BUCKET


VALID_DECISIONS = {"trade_candidate", "watchlist", "do_not_trade"}
VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}
VALID_OPTION_DIRECTIONS = {"call", "put", "neither"}
VALID_STOCK_DIRECTIONS = {"up", "down", "neutral"}
VALID_QUALITY_LEVELS = {"strong", "moderate", "weak"}
VALID_TIMING_CLARITY = {"clear", "unclear"}
VALID_TIME_HORIZONS = {*TIME_HORIZON_TO_DTE_BUCKET.keys(), "unclear"}

STRATEGIST_RECOMMENDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recommendation": {
            "type": "object",
            "properties": {
                "decision": {
                    "type": "string",
                    "enum": ["trade_candidate", "watchlist", "do_not_trade"],
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                },
                "evidence_quality": {
                    "type": "string",
                    "enum": ["strong", "moderate", "weak"],
                },
                "setup_quality": {
                    "type": "string",
                    "enum": ["strong", "moderate", "weak"],
                },
                "timing_clarity": {
                    "type": "string",
                    "enum": ["clear", "unclear"],
                },
                "preferred_option_direction": {
                    "type": "string",
                    "enum": ["call", "put", "neither"],
                },
                "expected_stock_direction": {
                    "type": "string",
                    "enum": ["up", "down", "neutral"],
                },
                "time_horizon": {
                    "type": "string",
                    "enum": list(VALID_TIME_HORIZONS),
                },
                "why_now": {"type": "string"},
                "summary": {"type": "string"},
                "catalyst": {"type": "array", "items": {"type": "string"}},
                "thesis": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
                "contradictions_present": {"type": "boolean"},
                "watchlist_reason": {"type": "string"},
            },
            "required": [
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
            ],
            "additionalProperties": False,
        }
    },
    "required": ["recommendation"],
    "additionalProperties": False,
}
