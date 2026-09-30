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
from agent_stages.strategist_schema import strategist_recommendation_schema
from services.evidence_thesis import (
    check_evidence_thesis,
    compact_thesis_check,
    not_requested_check,
    persist_thesis_check,
)
from services.evidence_thesis_policy import EvidenceThesisPolicy


OLLAMA_HOST = os.getenv(
    "STRATEGIST_OLLAMA_HOST",
    os.getenv("MACRO_NEWS_OLLAMA_HOST", os.getenv("WORLD_NEWS_OLLAMA_HOST", "http://127.0.0.1:11434")),
)
DEFAULT_MODEL = os.getenv(
    "STRATEGIST_MODEL",
    os.getenv("MACRO_NEWS_MODEL", os.getenv("WORLD_NEWS_MODEL", "world-news-sectors")),
)

_strategist_client: Client | None = None
STRATEGIST_PROMPT_VERSION = "grounded-strategist-v3-evidence-thesis"
LEGACY_STRATEGIST_PROMPT_VERSION = "grounded-strategist-v2-company-evidence"
THESIS_ROUTE = "legacy_strategist"


def strategist_prompt_version(policy: EvidenceThesisPolicy) -> str:
    return STRATEGIST_PROMPT_VERSION if policy.requests_thesis else LEGACY_STRATEGIST_PROMPT_VERSION


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
                "company_evidence",
            )
        }
    )


def get_default_client() -> Client:
    global _strategist_client
    if _strategist_client is None:
        _strategist_client = get_model_client(OLLAMA_HOST)
    return _strategist_client


def ask_model(
    client: Client,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    include_thesis: bool = False,
) -> str:
    return ask_llm_model(
        client,
        model,
        system_prompt,
        user_prompt,
        temperature=0,
        host_label=OLLAMA_HOST,
        response_schema=strategist_recommendation_schema(include_thesis=include_thesis),
    )


def _attach_thesis_check(
    recommendation: dict[str, Any],
    check: dict[str, Any],
    *,
    company: dict[str, Any],
    candidate_context: dict[str, Any] | None,
    model: str,
    prompt_version: str,
) -> dict[str, Any]:
    """Persist the full check and carry only the thesis plus a compact summary forward."""
    evaluation_id = persist_thesis_check(
        check,
        company_id=int(company["company_id"]),
        candidate_id=(candidate_context or {}).get("candidate_id"),
        context={"model": model, "prompt_version": prompt_version},
    )
    return {
        **recommendation,
        "evidence_thesis": check["thesis"],
        "evidence_thesis_check": compact_thesis_check(check, evaluation_id),
    }


def _save_strategist_summary(
    *,
    company: dict[str, Any],
    context_snapshot: dict[str, Any],
    recommendation: dict[str, Any],
    model: str,
    input_payload: dict[str, Any],
    prompt_version: str,
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
        prompt_version=prompt_version,
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
        "prompt_version": prompt_version,
        "input_article_ids": [int(article_id) for article_id in article_ids if isinstance(article_id, int)],
        "created_at": created_at,
    }


def _record_failed_strategist_run(
    *,
    company: dict[str, Any],
    input_payload: dict[str, Any],
    model: str,
    prompt_version: str,
    raw_response: str | None,
    error: Exception,
) -> None:
    initialize_news_database()
    grounding = input_payload.get("grounding") if isinstance(input_payload.get("grounding"), dict) else {}
    article_ids = grounding.get("article_ids") if isinstance(grounding.get("article_ids"), list) else []
    record_strategist_run(
        company_id=int(company["company_id"]),
        model=model,
        prompt_version=prompt_version,
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
    candidate_context: dict[str, Any] | None = None,
    thesis_policy: EvidenceThesisPolicy | None = None,
) -> dict[str, Any]:
    # Run the strategist stage end to end, including no-evidence short circuits and text fallback parsing.
    client = client or get_default_client()
    thesis_policy = thesis_policy or EvidenceThesisPolicy.from_env()
    include_thesis = thesis_policy.requests_thesis
    prompt_version = strategist_prompt_version(thesis_policy)
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

    from services.candidate_lifecycle import checkpoint
    payload["candidate_id"] = (candidate_context or {}).get("candidate_id")
    checkpoint(candidate_context, "pre_strategist", payload)
    company = payload["company"]
    context_snapshot = build_context_snapshot(payload)

    if not payload_has_evidence(payload):
        result = build_no_evidence_result(company, context_snapshot=context_snapshot)
        if include_thesis:
            result["recommendation"] = _attach_thesis_check(
                result["recommendation"],
                not_requested_check(payload, "no_evidence", route=THESIS_ROUTE, policy=thesis_policy),
                company=company, candidate_context=candidate_context, model=model, prompt_version=prompt_version,
            )
        strategist_run_reference = _save_strategist_summary(
            company=company,
            context_snapshot=context_snapshot,
            recommendation=result["recommendation"],
            model=model,
            input_payload=payload,
            prompt_version=prompt_version,
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
        include_thesis=include_thesis,
    )
    raw_response: str | None = None
    raw_recommendation: dict[str, Any] | None = None
    checkpoint(candidate_context, "strategist_prompt", {"model": model,
               "system_prompt": system_prompt, "user_prompt": user_prompt})
    try:
        raw_response = ask_model(
            client=client,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            include_thesis=include_thesis,
        )

        parsed = extract_json_value(raw_response)
        raw_recommendation = extract_recommendation(parsed)
        recommendation = normalize_recommendation(raw_recommendation)
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
            prompt_version=prompt_version,
            raw_response=raw_response,
            error=exc,
        )
        raise

    if include_thesis:
        # The legacy decision stands; the gate after the manager applies any deferral.
        recommendation = _attach_thesis_check(
            recommendation,
            check_evidence_thesis((raw_recommendation or {}).get("evidence_thesis"), payload,
                                  route=THESIS_ROUTE, policy=thesis_policy),
            company=company, candidate_context=candidate_context, model=model, prompt_version=prompt_version,
        )
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
        prompt_version=prompt_version,
        raw_response=raw_response,
    )
    return {
        **result,
        "grounding": payload.get("grounding", {}),
        "manager_evidence_payload": _build_manager_evidence_payload(payload),
        "strategist_run_reference": strategist_run_reference,
    }
