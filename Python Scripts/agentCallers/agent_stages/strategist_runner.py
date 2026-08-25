from __future__ import annotations

import copy
from datetime import datetime, timezone
import os
from typing import Any

from agent_builders.strategist_payload import (
    DEFAULT_FULL_ARTICLE_LIMIT,
    DEFAULT_MAX_ARTICLE_AGE_DAYS,
    DEFAULT_SUMMARY_ARTICLE_LIMIT,
    build_strategist_input,
)
from agent_helpers.shared import Client, ask_llm_model, extract_json_value, get_model_client
from db_helpers import add_strategist_company_summary, initialize_news_database, record_strategist_run
from agent_stages.strategist_grounding import validate_grounded_recommendation
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
STRATEGIST_PROMPT_VERSION = "grounded-strategist-v1"


def _build_manager_evidence_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Freeze the exact article evidence used for the strategist decision."""
    return copy.deepcopy(
        {
            key: payload.get(key)
            for key in (
                "company",
                "filters",
                "opportunist_rollup",
                "views",
                "supporting_articles",
                "grounding",
            )
        }
    )


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
    input_payload: dict[str, Any],
    raw_response: str | None = None,
) -> dict[str, Any]:
    # Persist the strategist summary and return a summary that callers can inspect.
    initialize_news_database()
    result_payload = {
        "company": company,
        "context_snapshot": context_snapshot,
        "recommendation": recommendation,
    }
    grounding = input_payload.get("grounding") if isinstance(input_payload.get("grounding"), dict) else {}
    article_ids = grounding.get("article_ids") if isinstance(grounding.get("article_ids"), list) else []
    created_at = datetime.now(timezone.utc).isoformat()
    run_id = record_strategist_run(
        company_id=int(company["company_id"]),
        model=model,
        prompt_version=STRATEGIST_PROMPT_VERSION,
        input_article_ids=[int(article_id) for article_id in article_ids if isinstance(article_id, int)],
        input_payload=input_payload,
        output_payload={"raw_response": raw_response, "recommendation": recommendation},
        status="completed",
        created_at=created_at,
    )
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
    return {
        "id": run_id,
        "model": model,
        "prompt_version": STRATEGIST_PROMPT_VERSION,
        "input_article_ids": [int(article_id) for article_id in article_ids if isinstance(article_id, int)],
        "created_at": created_at,
    }


def _record_failed_strategist_run(
    *,
    company: dict[str, Any],
    input_payload: dict[str, Any],
    model: str,
    raw_response: str | None,
    error: Exception,
) -> None:
    initialize_news_database()
    grounding = input_payload.get("grounding") if isinstance(input_payload.get("grounding"), dict) else {}
    article_ids = grounding.get("article_ids") if isinstance(grounding.get("article_ids"), list) else []
    record_strategist_run(
        company_id=int(company["company_id"]),
        model=model,
        prompt_version=STRATEGIST_PROMPT_VERSION,
        input_article_ids=[int(article_id) for article_id in article_ids if isinstance(article_id, int)],
        input_payload=input_payload,
        output_payload={"raw_response": raw_response} if raw_response is not None else None,
        status="failed",
        error_message=str(error),
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
    market_regime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Run the strategist stage end to end, including no-evidence short circuits and text fallback parsing.
    client = client or get_default_client()
    payload = build_strategist_input(
        company_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
        summary_article_limit=summary_article_limit,
        full_article_limit=full_article_limit,
    )
    if isinstance(market_regime, dict):
        payload["market_context"] = {"market_regime": market_regime}

    company = payload["company"]
    context_snapshot = build_context_snapshot(payload)

    if not payload_has_evidence(payload):
        result = build_no_evidence_result(company, context_snapshot=context_snapshot)
        strategist_run_reference = _save_strategist_summary(
            company=company,
            context_snapshot=context_snapshot,
            recommendation=result["recommendation"],
            model=model,
            input_payload=payload,
        )
        return {
            **result,
            "grounding": payload.get("grounding", {}),
            "manager_evidence_payload": _build_manager_evidence_payload(payload),
            "strategist_run_reference": strategist_run_reference,
        }

    system_prompt, user_prompt = build_strategist_prompt(
        payload,
        system_prompt_override=system_prompt_override,
        task_override=task_override,
    )
    raw_response: str | None = None
    try:
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
                "Expected a grounded JSON recommendation with evidence_claims. "
                f"Raw response: {raw_response[:800]}"
            )
        recommendation = validate_grounded_recommendation(
            recommendation,
            grounding=payload.get("grounding", {}),
        )
    except Exception as exc:
        _record_failed_strategist_run(
            company=company,
            input_payload=payload,
            model=model,
            raw_response=raw_response,
            error=exc,
        )
        raise

    result = {
        "company": company,
        "context_snapshot": context_snapshot,
        "recommendation": recommendation,
    }
    strategist_run_reference = _save_strategist_summary(
        company=company,
        context_snapshot=context_snapshot,
        recommendation=recommendation,
        model=model,
        input_payload=payload,
        raw_response=raw_response,
    )
    return {
        **result,
        "grounding": payload.get("grounding", {}),
        "manager_evidence_payload": _build_manager_evidence_payload(payload),
        "strategist_run_reference": strategist_run_reference,
    }
