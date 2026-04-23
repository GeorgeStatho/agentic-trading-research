from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


AGENT_STAGES_DIR = Path(__file__).resolve().parent
AGENT_CALLERS_DIR = AGENT_STAGES_DIR.parent
PYTHON_SCRIPTS_DIR = AGENT_CALLERS_DIR.parent
ROOT_DIR = PYTHON_SCRIPTS_DIR.parent
DATA_DIR = ROOT_DIR / "Data"
for path in (AGENT_CALLERS_DIR, PYTHON_SCRIPTS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.append(normalized)

from StrategistPayloadBuilder import (
    DEFAULT_FULL_ARTICLE_LIMIT,
    DEFAULT_MAX_ARTICLE_AGE_DAYS,
    DEFAULT_SUMMARY_ARTICLE_LIMIT,
    build_strategist_input,
)
from _shared import Client, ask_llm_model, extract_json_value, get_model_client
from db_helpers import add_strategist_company_summary, initialize_news_database


OLLAMA_HOST = os.getenv(
    "STRATEGIST_OLLAMA_HOST",
    os.getenv("MACRO_NEWS_OLLAMA_HOST", os.getenv("WORLD_NEWS_OLLAMA_HOST", "http://127.0.0.1:11434")),
)
DEFAULT_MODEL = os.getenv(
    "STRATEGIST_MODEL",
    os.getenv("MACRO_NEWS_MODEL", os.getenv("WORLD_NEWS_MODEL", "world-news-sectors")),
)

VALID_DECISIONS = {"trade_candidate", "watchlist", "do_not_trade"}
VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}
VALID_OPTION_DIRECTIONS = {"call", "put", "neither"}
VALID_STOCK_DIRECTIONS = {"up", "down", "neutral"}
VALID_QUALITY_LEVELS = {"strong", "moderate", "weak"}
VALID_TIMING_CLARITY = {"clear", "unclear"}
VALID_TIME_HORIZONS = {"very_short_term", "short_term", "medium_term", "unclear"}

_strategist_client: Client | None = None
STRATEGIST_RECOMMENDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recommendation": {
            "type": "object",
            "properties": {
                "decision": {
                    "type": "string",
                    "enum": ["trade_candidate", "watchlist", "do_not_trade"]
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"]
                },
                "evidence_quality": {
                    "type": "string",
                    "enum": ["strong", "moderate", "weak"]
                },
                "setup_quality": {
                    "type": "string",
                    "enum": ["strong", "moderate", "weak"]
                },
                "timing_clarity": {
                    "type": "string",
                    "enum": ["clear", "unclear"]
                },
                "preferred_option_direction": {
                    "type": "string",
                    "enum": ["call", "put", "neither"]
                },
                "expected_stock_direction": {
                    "type": "string",
                    "enum": ["up", "down", "neutral"]
                },
                "time_horizon": {
                    "type": "string",
                    "enum": ["very_short_term", "short_term", "medium_term", "unclear"]
                },
                "why_now": {"type": "string"},
                "summary": {"type": "string"},
                "catalyst": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "thesis": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "risks": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "contradictions_present": {"type": "boolean"},
                "watchlist_reason": {"type": "string"}
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
                "watchlist_reason"
            ],
            "additionalProperties": False
        }
    },
    "required": ["recommendation"],
    "additionalProperties": False
}

__all__ = [
    "build_strategist_prompt",
    "decide_company_purchase",
]


def _get_default_client() -> Client:
    global _strategist_client
    if _strategist_client is None:
        _strategist_client = get_model_client(OLLAMA_HOST)
    return _strategist_client


def ask_model(client: Client, model: str, system_prompt: str, user_prompt: str) -> str:
    return ask_llm_model(
        client,
        model,
        system_prompt,
        user_prompt,
        temperature=0,
        host_label=OLLAMA_HOST,
        response_schema=STRATEGIST_RECOMMENDATION_SCHEMA,
    )


def _payload_has_evidence(payload: dict[str, Any]) -> bool:
    view_total = 0
    for view in payload.get("views", {}).values():
        try:
            view_total += int(view.get("count") or 0)
        except (TypeError, ValueError):
            continue

    supporting_articles = payload.get("supporting_articles", {})
    article_summaries = supporting_articles.get("article_summaries", [])
    full_articles = supporting_articles.get("full_articles", [])
    return view_total > 0 or bool(article_summaries) or bool(full_articles)


def _build_context_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    historical_price_data = payload.get("company", {}).get("historical_price_data", {})
    return {
        "view_counts": {
            key: int(value.get("count") or 0)
            for key, value in payload.get("views", {}).items()
            if isinstance(value, dict)
        },
        "article_summary_count": len(payload.get("supporting_articles", {}).get("article_summaries", [])),
        "full_article_count": len(payload.get("supporting_articles", {}).get("full_articles", [])),
        "historical_periods_available": sum(
            1
            for item in historical_price_data.values()
            if isinstance(item, dict) and item.get("available")
        ),
    }


def build_strategist_prompt(
    payload: dict[str, Any],
    *,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> tuple[str, str]:
    default_system_prompt = (
        "You are an investment strategist deciding whether a company currently supports opening an options trade candidate on the underlying equity. "
    "Use only the supplied structured context. Do not invent facts, catalysts, prices, or risks that are not supported by the input. "
    "Treat upstream agent conclusions as signals, not truth. Weigh them against the article evidence, evidence freshness, contradiction level, "
    "and the supplied 1d, 5d, 1mo, and 3mo historical price action. "
    "When the evidence is strong, fresh, specific, and internally consistent, prefer that evidence over a noisy single-day move or weak short-term price action. "
    "Use recent price action mainly to judge timing quality, volatility, and whether the setup is tradable now, not to override a very convincing evidence-based thesis unless the price action clearly contradicts it. "
    "Your job is not to choose stock shares and not to select a specific option contract. "
    "Your job is to decide whether the company should be passed forward as: trade_candidate, watchlist, or do_not_trade. "
    "A trade_candidate should have sufficiently strong evidence, a reasonably clear directional bias, and enough timing clarity to justify evaluating an options trade now. "
    "A watchlist decision should be used when the thesis may be promising but the evidence, timing, or setup quality is not yet strong enough for immediate trade consideration. "
    "A do_not_trade decision should be used when the evidence is weak, stale, contradictory, low-quality, mostly negative, or directionally unclear. "
    "Be selective but willing to take reasonable risk when the opportunity looks genuinely strong. If the evidence is strong, the directional bias is clear, and the setup looks actionable, lean toward trade_candidate rather than staying too passive. "
    "If the evidence is mixed or timing is unclear, still prefer watchlist or do_not_trade over trade_candidate. "
    "Express the directional view twice: expected_stock_direction as up, down, or neutral; and preferred_option_direction as call, put, or neither. "
    "These fields must be internally consistent with the decision. If there is no actionable options setup, preferred_option_direction should usually be neither. "
    "Distinguish between evidence quality and setup quality: a company can have an interesting story but still be a poor trade setup. "
    "why_now must explain why the setup is actionable now, or why it is not actionable now. "
    "summary must be a short paragraph. thesis, catalyst, and risks must be concise lists of short strings. "
    "Return only valid JSON with a top-level key named recommendation. "
    "Do not include markdown fences, explanations, or extra keys."
    )
    system_prompt = str(system_prompt_override or default_system_prompt)
    task_prompt = (
        "Evaluate the company using the provided structured context and return JSON matching the required schema.\n"
        "\n"
        "Field guidance:\n"
        "- decision: trade_candidate, watchlist, or do_not_trade\n"
        "- confidence: overall confidence in this recommendation\n"
        "- evidence_quality: strength and credibility of the supporting evidence\n"
        "- setup_quality: quality of the current trade setup for options, considering direction clarity and timing\n"
        "- timing_clarity: whether the expected move appears actionable now\n"
        "- preferred_option_direction: call, put, or neither\n"
        "- expected_stock_direction: up, down, or neutral\n"
        "- time_horizon: expected time frame for the thesis to matter\n"
        "- why_now: one short explanation of why the setup is actionable now, or why it is not\n"
        "- summary: short paragraph\n"
        "- catalyst: concrete possible drivers or near-term triggers from the input\n"
        "- thesis: strongest reasons supporting the recommendation\n"
        "- risks: strongest reasons the trade could fail or should be avoided\n"
        "- contradictions_present: true if meaningful conflicting evidence exists\n"
        "- watchlist_reason: explain what is missing before this could become a trade_candidate; empty string if not applicable\n"
    )

    user_payload = {
        "task": str(task_override or task_prompt),
        "company": payload["company"],
        "peer_groups": payload.get("peer_groups", {}),
        "filters": payload.get("filters", {}),
        "views": payload.get("views", {}),
        "supporting_articles": payload.get("supporting_articles", {}),
        "required_output": {
            "recommendation": {
                "decision": "trade_candidate|watchlist|do_not_trade",
                "confidence": "high|medium|low",
                "evidence_quality": "strong|moderate|weak",
                "setup_quality": "strong|moderate|weak",
                "timing_clarity": "clear|unclear",
                "preferred_option_direction": "call|put|neither",
                "expected_stock_direction": "up|down|neutral",
                "time_horizon": "very_short_term|short_term|medium_term|unclear",
                "why_now": "short explanation",
                "summary": "short paragraph",
                "catalyst": ["short point"],
                "thesis": ["short point"],
                "risks": ["short point"],
                "contradictions_present": True,
                "watchlist_reason": "short explanation or empty string",
            }
        },
    }
    return system_prompt, json.dumps(user_payload, ensure_ascii=True)


def _extract_recommendation(payload: dict[str, Any] | None) -> dict[str, Any] | None:
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


def _normalize_recommendation(recommendation: dict[str, Any] | None) -> dict[str, Any] | None:
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


def _extract_recommendation_from_text(raw_response: str) -> dict[str, Any] | None:
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

    return _normalize_recommendation(recommendation)


def _build_no_evidence_result(company: dict[str, Any], *, context_snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "company": company,
        "context_snapshot": context_snapshot,
        "recommendation": {
            "decision": "do_not_trade",
            "confidence": "low",
            "evidence_quality": "weak",
            "setup_quality": "weak",
            "timing_clarity": "unclear",
            "preferred_option_direction": "neither",
            "expected_stock_direction": "neutral",
            "time_horizon": "unclear",
            "why_now": "There is not enough processed evidence to justify an actionable near-term options setup.",
            "summary": "There was not enough processed article evidence available to support opening an options trade on this underlying.",
            "catalyst": [],
            "thesis": [],
            "risks": ["Insufficient supporting evidence from macro, sector, industry, and company article analysis."],
            "contradictions_present": False,
            "watchlist_reason": "",
        },
    }


def _save_strategist_summary(
    *,
    company: dict[str, Any],
    context_snapshot: dict[str, Any],
    recommendation: dict[str, Any],
    model: str,
) -> None:
    initialize_news_database()
    result_payload = {
        "company": company,
        "context_snapshot": context_snapshot,
        "recommendation": recommendation,
    }
    add_strategist_company_summary(
        company_id=int(company["company_id"]),
        decision=str(recommendation.get("decision") or ""),
        confidence=str(recommendation.get("confidence") or ""),
        summary=str(recommendation.get("summary") or ""),
        thesis=recommendation.get("thesis", []),
        risks=recommendation.get("risks", []),
        model=model,
        raw_json=result_payload,
    )


def decide_company_purchase(
    company_identifier: str,
    *,
    client: Client | None = None,
    model: str = DEFAULT_MODEL,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
    summary_article_limit: int = DEFAULT_SUMMARY_ARTICLE_LIMIT,
    full_article_limit: int = DEFAULT_FULL_ARTICLE_LIMIT,
) -> dict[str, Any]:
    client = client or _get_default_client()
    payload = build_strategist_input(
        company_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
        summary_article_limit=summary_article_limit,
        full_article_limit=full_article_limit,
    )

    company = payload["company"]
    context_snapshot = _build_context_snapshot(payload)

    if not _payload_has_evidence(payload):
        result = _build_no_evidence_result(company, context_snapshot=context_snapshot)
        _save_strategist_summary(
            company=company,
            context_snapshot=context_snapshot,
            recommendation=result["recommendation"],
            model=model,
        )
        return result

    system_prompt, user_prompt = build_strategist_prompt(
        payload,
        system_prompt_override=system_prompt_override,
        task_override=task_override,
    )
    raw_response = ask_model(
        client=client,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    parsed = extract_json_value(raw_response)
    recommendation = _normalize_recommendation(_extract_recommendation(parsed))
    if recommendation is None:
        recommendation = _extract_recommendation_from_text(raw_response)
    if recommendation is None:
        raise RuntimeError(
            "Strategist model returned an invalid response. "
            "Expected a JSON object with recommendation.decision/confidence/summary/thesis/risks. "
            f"Raw response: {raw_response[:800]}"
        )

    result = {
        "company": company,
        "context_snapshot": context_snapshot,
        "recommendation": recommendation,
    }
    _save_strategist_summary(
        company=company,
        context_snapshot=context_snapshot,
        recommendation=recommendation,
        model=model,
    )
    return result


if __name__ == "__main__":
    print(json.dumps(decide_company_purchase("AAPL"), ensure_ascii=True, indent=2))
