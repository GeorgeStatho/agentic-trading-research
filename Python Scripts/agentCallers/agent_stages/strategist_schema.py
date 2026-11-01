from __future__ import annotations

from typing import Any

from services.options.option_dte_buckets import TIME_HORIZON_TO_DTE_BUCKET
from agent_stages.evidence_thesis_contract import EVIDENCE_THESIS_SCHEMA


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
                "grounded_company_symbol": {"type": "string"},
                "grounded_entity_ids": {
                    "type": "object",
                    "properties": {
                        "company_id": {"type": "integer"},
                        "industry_id": {"type": "integer"},
                        "sector_id": {"type": "integer"},
                    },
                    "required": ["company_id", "industry_id", "sector_id"],
                    "additionalProperties": False,
                },
                "evidence_claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim_type": {
                                "type": "string",
                                "enum": [
                                    "summary",
                                    "why_now",
                                    "catalyst",
                                    "thesis",
                                    "risk",
                                    "watchlist_reason",
                                ],
                            },
                            "claim": {"type": "string"},
                            "evidence_article_ids": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "minItems": 1,
                            },
                        },
                        "required": ["claim_type", "claim", "evidence_article_ids"],
                        "additionalProperties": False,
                    },
                },
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
                "grounded_company_symbol",
                "grounded_entity_ids",
                "evidence_claims",
            ],
            "additionalProperties": False,
        }
    },
    "required": ["recommendation"],
    "additionalProperties": False,
}


def strategist_recommendation_schema(*, include_thesis: bool) -> dict[str, Any]:
    """The legacy schema, optionally extended with the versioned evidence thesis."""
    if not include_thesis:
        return STRATEGIST_RECOMMENDATION_SCHEMA

    legacy = STRATEGIST_RECOMMENDATION_SCHEMA["properties"]["recommendation"]
    recommendation = {
        **legacy,
        "properties": {**legacy["properties"], "evidence_thesis": EVIDENCE_THESIS_SCHEMA},
        "required": [*legacy["required"], "evidence_thesis"],
    }
    return {**STRATEGIST_RECOMMENDATION_SCHEMA, "properties": {"recommendation": recommendation}}
