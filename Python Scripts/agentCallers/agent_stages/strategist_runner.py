from __future__ import annotations

from datetime import datetime
import os
from typing import Any

from agent_builders.strategist_payload import (
    DEFAULT_FULL_ARTICLE_LIMIT,
    DEFAULT_MAX_ARTICLE_AGE_DAYS,
    DEFAULT_SUMMARY_ARTICLE_LIMIT,
    build_strategist_input,
)
from agent_helpers.shared import Client, ask_llm_model, extract_json_value, get_model_client
from db_helpers import add_strategist_company_summary, initialize_news_database
from agent_stages.strategist_normalization import (
    extract_recommendation,
    extract_recommendation_from_text,
    normalize_recommendation,
)
from agent_stages.strategist_prompt import (
    build_context_snapshot,
    build_no_evidence_result,
    build_strategist_prompt,
    payload_has_evidence,
)
from agent_stages.strategist_schema import STRATEGIST_RECOMMENDATION_SCHEMA


OLLAMA_HOST = os.getenv(
    "STRATEGIST_OLLAMA_HOST",
    os.getenv("MACRO_NEWS_OLLAMA_HOST", os.getenv("WORLD_NEWS_OLLAMA_HOST", "http://127.0.0.1:11434")),
)
DEFAULT_MODEL = os.getenv(
    "STRATEGIST_MODEL",
    os.getenv("MACRO_NEWS_MODEL", os.getenv("WORLD_NEWS_MODEL", "world-news-sectors")),
)

_strategist_client: Client | None = None


def get_default_client() -> Client:
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
    client = client or get_default_client()
    payload = build_strategist_input(
        company_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
        summary_article_limit=summary_article_limit,
        full_article_limit=full_article_limit,
    )

    company = payload["company"]
    context_snapshot = build_context_snapshot(payload)

    if not payload_has_evidence(payload):
        result = build_no_evidence_result(company, context_snapshot=context_snapshot)
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
    recommendation = normalize_recommendation(extract_recommendation(parsed))
    if recommendation is None:
        recommendation = extract_recommendation_from_text(raw_response)
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
