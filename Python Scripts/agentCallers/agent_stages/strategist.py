from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

AGENT_CALLERS_DIR = Path(__file__).resolve().parent
if str(AGENT_CALLERS_DIR) not in sys.path:
    sys.path.append(str(AGENT_CALLERS_DIR))

from StrategistPayloadBuilder import (
    DEFAULT_FULL_ARTICLE_LIMIT,
    DEFAULT_MAX_ARTICLE_AGE_DAYS,
    DEFAULT_SUMMARY_ARTICLE_LIMIT,
    build_strategist_input,
)
from _shared import Client, ask_ollama_model, extract_json_value, get_ollama_client


OLLAMA_HOST = os.getenv(
    "STRATEGIST_OLLAMA_HOST",
    os.getenv("MACRO_NEWS_OLLAMA_HOST", os.getenv("WORLD_NEWS_OLLAMA_HOST", "http://127.0.0.1:11434")),
)
DEFAULT_MODEL = os.getenv(
    "STRATEGIST_MODEL",
    os.getenv("MACRO_NEWS_MODEL", os.getenv("WORLD_NEWS_MODEL", "world-news-sectors")),
)

VALID_DECISIONS = {"buy", "do_not_buy"}
VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}

strategist = get_ollama_client(OLLAMA_HOST)
STRATEGIST_RECOMMENDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recommendation": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["buy", "do_not_buy"]},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "summary": {"type": "string"},
                "thesis": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "risks": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["decision", "confidence", "summary", "thesis", "risks"],
            "additionalProperties": False,
        }
    },
    "required": ["recommendation"],
    "additionalProperties": False,
}

__all__ = [
    "build_strategist_prompt",
    "decide_company_purchase",
]


def ask_model(client: Client, model: str, system_prompt: str, user_prompt: str) -> str:
    return ask_ollama_model(
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
    return {
        "view_counts": {
            key: int(value.get("count") or 0)
            for key, value in payload.get("views", {}).items()
            if isinstance(value, dict)
        },
        "article_summary_count": len(payload.get("supporting_articles", {}).get("article_summaries", [])),
        "full_article_count": len(payload.get("supporting_articles", {}).get("full_articles", [])),
    }


def build_strategist_prompt(
    payload: dict[str, Any],
    *,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> tuple[str, str]:
    default_system_prompt = (
        "You are an investment strategist deciding whether a company is currently a buy or not a buy. "
        "Use only the supplied structured context. "
        "Treat upstream agent conclusions as signals, not certainty, and weigh them against the article evidence. "
        "If the evidence is mixed, weak, or mostly negative, prefer 'do_not_buy'. "
        "Return only valid JSON with a top-level key named 'recommendation'. "
        "Do not include markdown fences, notes, or extra keys. "
        "The recommendation object must contain: decision, confidence, summary, thesis, and risks. "
        "decision must be one of: buy, do_not_buy. "
        "confidence must be one of: high, medium, low. "
        "summary must be a short paragraph. "
        "thesis must be a short list of bullish supporting points. "
        "risks must be a short list of reasons the company should not be bought or should be watched carefully."
    )
    system_prompt = str(system_prompt_override or default_system_prompt)

    user_payload = {
        "task": str(task_override or "Decide whether the supplied company is a buy right now using the layered strategist context."),
        "company": payload["company"],
        "peer_groups": payload.get("peer_groups", {}),
        "filters": payload.get("filters", {}),
        "views": payload.get("views", {}),
        "supporting_articles": payload.get("supporting_articles", {}),
        "required_output": {
            "recommendation": {
                "decision": "buy|do_not_buy",
                "confidence": "high|medium|low",
                "summary": "short paragraph",
                "thesis": ["short point"],
                "risks": ["short point"],
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
        for key in ("decision", "confidence", "summary", "thesis", "risks")
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
                for inner_key in ("decision", "confidence", "summary", "thesis", "risks")
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
        "no_buy": "do_not_buy",
        "not_buy": "do_not_buy",
        "dont_buy": "do_not_buy",
        "don't_buy": "do_not_buy",
        "do not buy": "do_not_buy",
        "not a buy": "do_not_buy",
        "avoid": "do_not_buy",
        "hold": "do_not_buy",
        "pass": "do_not_buy",
    }
    decision = replacements.get(decision, decision)
    if decision in VALID_DECISIONS:
        return decision
    if "buy" in decision and "not" not in decision and "no" not in decision:
        return "buy"
    if any(token in decision for token in ("not", "avoid", "hold", "pass")):
        return "do_not_buy"
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


def _normalize_recommendation(recommendation: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(recommendation, dict):
        return None

    decision = _normalize_decision(recommendation.get("decision"))
    confidence = _normalize_confidence(recommendation.get("confidence"))
    summary = str(recommendation.get("summary") or recommendation.get("reason") or "").strip()
    thesis = _normalize_string_list(recommendation.get("thesis"))
    risks = _normalize_string_list(recommendation.get("risks"))

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

    if decision not in VALID_DECISIONS:
        return None
    if confidence not in VALID_CONFIDENCE_LEVELS:
        return None
    if not summary:
        return None

    return {
        "decision": decision,
        "confidence": confidence,
        "summary": summary,
        "thesis": thesis,
        "risks": risks,
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
        r"recommendation\s*:?\s*(buy|do[_\s-]*not[_\s-]*buy|hold|avoid|pass)",
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
            "decision": "do_not_buy",
            "confidence": "low",
            "summary": "There was not enough processed article evidence available to support a buy decision.",
            "thesis": [],
            "risks": ["Insufficient supporting evidence from macro, sector, industry, and company article analysis."],
        },
    }


def decide_company_purchase(
    company_identifier: str,
    *,
    client: Client = strategist,
    model: str = DEFAULT_MODEL,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
    summary_article_limit: int = DEFAULT_SUMMARY_ARTICLE_LIMIT,
    full_article_limit: int = DEFAULT_FULL_ARTICLE_LIMIT,
) -> dict[str, Any]:
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
        return _build_no_evidence_result(company, context_snapshot=context_snapshot)

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

    return {
        "company": company,
        "context_snapshot": context_snapshot,
        "recommendation": recommendation,
    }


if __name__ == "__main__":
    print(json.dumps(decide_company_purchase("AAPL"), ensure_ascii=True, indent=2))
