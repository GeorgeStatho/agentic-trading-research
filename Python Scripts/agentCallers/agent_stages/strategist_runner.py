from __future__ import annotations

import copy
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
from typing import Any

from services.config import AgentPipelineSettings
from services.settings import StageModel, get_settings
from agent_builders.strategist_payload import (
    DEFAULT_FULL_ARTICLE_LIMIT,
    DEFAULT_MAX_ARTICLE_AGE_DAYS,
    DEFAULT_SUMMARY_ARTICLE_LIMIT,
    build_strategist_input,
)
from agent_helpers.shared import Client, ask_llm_model, extract_json_value, get_model_client
from news_db_modules.impacts import add_strategist_company_summary
from news_db_modules.bootstrap import initialize_database as initialize_news_database
from news_db_modules.strategist_runs import latest_answered_strategist_run, record_strategist_run
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
from agent_contracts import StrategistResult
from agent_stages.strategist_schema import strategist_recommendation_schema
from services.evidence_thesis import (
    check_evidence_thesis,
    compact_thesis_check,
    not_requested_check,
    persist_thesis_check,
)
from services.evidence_thesis_policy import EvidenceThesisPolicy


LOGGER = logging.getLogger(__name__)
EVIDENCE_SECTIONS = ("views", "supporting_articles", "opportunist_rollup", "company_evidence")


def _stage_model() -> StageModel:
    return get_settings().models.strategist


_strategist_client: Client | None = None
STRATEGIST_PROMPT_VERSION = "grounded-strategist-v4-evidence-thesis-compact"
LEGACY_STRATEGIST_PROMPT_VERSION = "grounded-strategist-v3-company-evidence-compact"
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
        _strategist_client = get_model_client(_stage_model().ollama_host, _stage_model().provider)
    return _strategist_client


def ask_model(
    client: Client,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    include_thesis: bool = False,
    response_diagnostic: dict[str, Any] | None = None,
) -> str:
    return ask_llm_model(
        client,
        model,
        system_prompt,
        user_prompt,
        temperature=0,
        host_label=_stage_model().ollama_host,
        thinking_level=_stage_model().thinking_level,
        response_schema=strategist_recommendation_schema(include_thesis=include_thesis),
        response_diagnostic=response_diagnostic,
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
    return attach_thesis_check(recommendation, check, evaluation_id)


def _save_strategist_summary(
    *,
    company: dict[str, Any],
    context_snapshot: dict[str, Any],
    recommendation: dict[str, Any],
    model: str,
    input_payload: dict[str, Any],
    prompt_version: str,
    raw_response: str | None = None,
    reused_from_run_id: int | None = None,
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
    output_payload: dict[str, Any] = {"raw_response": raw_response, "recommendation": recommendation}
    if reused_from_run_id is not None:
        output_payload["reused_from_run_id"] = reused_from_run_id
    run_id = record_strategist_run(
        company_id=int(company["company_id"]),
        model=model,
        prompt_version=prompt_version,
        input_article_ids=[int(article_id) for article_id in article_ids if isinstance(article_id, int)],
        input_payload=input_payload,
        output_payload=output_payload,
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
    reference = {
        "id": run_id,
        "model": model,
        "prompt_version": prompt_version,
        "input_article_ids": [int(article_id) for article_id in article_ids if isinstance(article_id, int)],
        "created_at": created_at,
    }
    if reused_from_run_id is not None:
        reference["reused_from_run_id"] = reused_from_run_id
    return reference


def _strategist_evidence_hash(payload: dict[str, Any]) -> str:
    grounding = payload.get("grounding") if isinstance(payload.get("grounding"), dict) else {}
    evidence = {key: payload.get(key) for key in EVIDENCE_SECTIONS}
    evidence["article_ids"] = sorted(grounding.get("article_ids") or [], key=str)
    # Round-trip first so a live payload hashes like its stored copy (int dict keys become strings).
    stored_form = json.loads(json.dumps(evidence, default=str))
    return sha256(json.dumps(stored_form, sort_keys=True).encode()).hexdigest()


def _regime_state(payload: dict[str, Any]) -> Any:
    regime = (payload.get("market_context") or {}).get("market_regime")
    return regime.get("state") if isinstance(regime, dict) else None


def _last_price(payload: dict[str, Any]) -> float | None:
    price = ((payload.get("company") or {}).get("market_data") or {}).get("last_price")
    return float(price) if isinstance(price, (int, float)) and price > 0 else None


def strategist_answer_reusable(
    previous: dict[str, Any],
    payload: dict[str, Any],
    *,
    age_minutes: float,
    max_age_minutes: int,
    max_price_move_pct: float,
) -> bool:
    """Whether the answer to `previous` still stands for `payload`: same evidence and regime, little price move.

    Shared by live runs and backtest replays so both reuse under one rule.
    """
    if max_age_minutes <= 0 or not 0 <= age_minutes < max_age_minutes:
        return False
    if _strategist_evidence_hash(previous) != _strategist_evidence_hash(payload):
        return False
    if _regime_state(previous) != _regime_state(payload):
        return False
    previous_price, price = _last_price(previous), _last_price(payload)
    if previous_price is None or price is None:
        return False
    return abs(price / previous_price - 1) * 100 < max_price_move_pct


def _reusable_strategist_answer(
    payload: dict[str, Any],
    *,
    model: str,
    prompt_version: str,
    settings: AgentPipelineSettings | None = None,
) -> tuple[dict[str, Any], float] | None:
    """The latest model answer for this company when its evidence and timing context still hold, else None.

    Age and price move are measured against that original answer, so reuses never extend each other.
    """
    settings = settings or AgentPipelineSettings.from_env()
    if settings.strategist_reuse_max_age_minutes <= 0:
        return None
    anchor = latest_answered_strategist_run(int(payload["company"]["company_id"]))
    if anchor is None or anchor["model"] != model or anchor["prompt_version"] != prompt_version:
        return None
    if not isinstance((anchor["output_payload"] or {}).get("recommendation"), dict):
        return None
    answered_at = datetime.fromisoformat(anchor["created_at"])
    if answered_at.tzinfo is None:
        answered_at = answered_at.replace(tzinfo=timezone.utc)
    age_minutes = (datetime.now(timezone.utc) - answered_at).total_seconds() / 60
    if not strategist_answer_reusable(anchor["input_payload"], payload, age_minutes=age_minutes,
                                      max_age_minutes=settings.strategist_reuse_max_age_minutes,
                                      max_price_move_pct=settings.strategist_reuse_max_price_move_pct):
        return None
    return anchor, round(age_minutes, 1)


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


def _parse_strategist_response(raw_response: str, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
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
    return validate_grounded_recommendation(recommendation, grounding=payload.get("grounding", {})), raw_recommendation


def decide_strategist_recommendation(
    payload: dict[str, Any],
    *,
    client: Client,
    model: str,
    thesis_policy: EvidenceThesisPolicy,
    candidate_context: dict[str, Any] | None = None,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> dict[str, Any]:
    """The strategist's decision on a finished payload, shared by live runs and replays.

    Nothing is persisted: the thesis check comes back unsaved for the caller to keep or drop.
    """
    include_thesis = thesis_policy.requests_thesis
    if not payload_has_evidence(payload):
        no_evidence = build_no_evidence_result(payload["company"], context_snapshot=build_context_snapshot(payload))
        thesis_check = (not_requested_check(payload, "no_evidence", route=THESIS_ROUTE, policy=thesis_policy)
                        if include_thesis else None)
        return {"recommendation": no_evidence["recommendation"], "thesis_check": thesis_check,
                "raw_response": None, "no_evidence": True}

    system_prompt, user_prompt = build_strategist_prompt(
        payload,
        system_prompt_override=system_prompt_override,
        task_override=task_override,
        include_thesis=include_thesis,
    )
    from services.candidate_lifecycle import checkpoint
    checkpoint(candidate_context, "strategist_prompt", {"model": model,
               "system_prompt": system_prompt, "user_prompt": user_prompt})
    response_diagnostic: dict[str, Any] = {}
    raw_response = ask_model(client=client, model=model, system_prompt=system_prompt,
                             user_prompt=user_prompt, include_thesis=include_thesis,
                             response_diagnostic=response_diagnostic)
    try:
        recommendation, raw_recommendation = _parse_strategist_response(raw_response, payload)
        thesis_check = (check_evidence_thesis((raw_recommendation or {}).get("evidence_thesis"), payload,
                                              route=THESIS_ROUTE, policy=thesis_policy)
                        if include_thesis else None)
    except Exception as exc:
        # Carried on the original exception so live can record the failed run without changing its type.
        exc.raw_response = raw_response
        raise
    return {"recommendation": recommendation, "thesis_check": thesis_check,
            "raw_response": raw_response, "no_evidence": False, "response_diagnostic": response_diagnostic}


def replay_strategist_decision_from_payload(
    payload: dict[str, Any],
    *,
    client: Client | None = None,
    model: str | None = None,
    thesis_policy: EvidenceThesisPolicy | None = None,
) -> dict[str, Any]:
    """Re-run only the strategist LLM against a frozen decision-time payload.

    Nothing is saved: no strategist run, company summary or thesis check. A shadow evaluation
    therefore cannot replace the latest summary production reads; the caller persists the check.
    """
    model = model or _stage_model().model
    thesis_policy = thesis_policy or EvidenceThesisPolicy.from_env()
    decision = decide_strategist_recommendation(
        copy.deepcopy(payload),
        client=client or get_default_client(),
        model=model,
        thesis_policy=thesis_policy,
    )
    result = {"model": model, "prompt_version": strategist_prompt_version(thesis_policy),
              "thesis_check": decision["thesis_check"], "recommendation": decision["recommendation"]}
    if decision["no_evidence"]:
        result["replay_skipped"] = "no_evidence"
    return result


def attach_thesis_check(recommendation: dict[str, Any], check: dict[str, Any], evaluation_id: str | None) -> dict[str, Any]:
    return {**recommendation, "evidence_thesis": check["thesis"],
            "evidence_thesis_check": compact_thesis_check(check, evaluation_id)}


def decide_company_purchase(
    company_identifier: str,
    *,
    client: Client | None = None,
    model: str | None = None,
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
) -> StrategistResult:
    # Run the strategist stage end to end, including no-evidence short circuits and text fallback parsing.
    model = model or _stage_model().model
    client = client or get_default_client()
    thesis_policy = thesis_policy or EvidenceThesisPolicy.from_env()
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

    reuse = None
    if system_prompt_override is None and task_override is None and payload_has_evidence(payload):
        reuse = _reusable_strategist_answer(payload, model=model, prompt_version=prompt_version)
    if reuse is not None:
        # Unchanged evidence and timing context: the earlier answer, with its thesis check, stands.
        anchor, age_minutes = reuse
        recommendation = copy.deepcopy(anchor["output_payload"]["recommendation"])
        raw_response = None
        reused_from_run_id = anchor["id"]
        response_diagnostic = {"reused_from_run_id": reused_from_run_id, "reused_answer_age_minutes": age_minutes}
        LOGGER.info("Reusing strategist run %s (%.1f minutes old) for %s: evidence unchanged",
                    reused_from_run_id, age_minutes, company.get("symbol"))
    else:
        try:
            decision = decide_strategist_recommendation(
                payload,
                client=client,
                model=model,
                thesis_policy=thesis_policy,
                candidate_context=candidate_context,
                system_prompt_override=system_prompt_override,
                task_override=task_override,
            )
        except Exception as exc:
            _record_failed_strategist_run(
                company=company,
                input_payload=payload,
                model=model,
                prompt_version=prompt_version,
                raw_response=getattr(exc, "raw_response", None),
                error=exc,
            )
            raise

        recommendation = decision["recommendation"]
        if decision["thesis_check"] is not None:
            # The legacy decision stands; the gate after the manager applies any deferral.
            recommendation = _attach_thesis_check(
                recommendation,
                decision["thesis_check"],
                company=company, candidate_context=candidate_context, model=model, prompt_version=prompt_version,
            )
        raw_response = decision["raw_response"]
        reused_from_run_id = None
        response_diagnostic = decision.get("response_diagnostic", {})
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
        reused_from_run_id=reused_from_run_id,
    )
    return {
        **result,
        "grounding": payload.get("grounding", {}),
        "manager_evidence_payload": _build_manager_evidence_payload(payload),
        "strategist_run_reference": strategist_run_reference,
        "response_diagnostic": response_diagnostic,
    }
