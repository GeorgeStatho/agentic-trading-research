from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from agent_helpers.manager import (
    DEFAULT_FULL_ARTICLE_LIMIT,
    DEFAULT_MAX_ARTICLE_AGE_DAYS,
    DEFAULT_OPTION_CHAIN_LIMIT_PER_TYPE,
    DEFAULT_SUMMARY_ARTICLE_LIMIT,
    build_manager_input,
    build_market_context,
)
from agent_helpers.shared import Client, extract_json_value
from .manager_payload_context import (
    _build_context_snapshot,
    payload_has_evidence,
)
from .manager_strategist_context import _build_manager_visible_strategist_context
from .manager_normalization import (
    extract_recommendation,
    extract_recommendation_from_text,
    normalize_recommendation,
)
from .manager_prompt import build_manager_prompt
from .manager_shared import (
    DEFAULT_MODEL,
    LOGGER,
    MANAGER_PROMPT_VERSION,
    MANAGER_STAGE_VERSION,
    ask_model,
    get_default_client,
    normalize_decision,
    normalize_target_dte_bucket,
    target_dte_bucket_to_expiration_filters,
)


def _refresh_market_context_for_target_dte_bucket(
    *,
    company: dict[str, Any],
    market_context: dict[str, Any],
    recommendation: dict[str, Any],
    option_expiration_date: str | None,
    option_expiration_date_gte: str | None,
    option_expiration_date_lte: str | None,
    option_strike_price_gte: float | None,
    option_strike_price_lte: float | None,
    option_contract_limit_per_type: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    decision = normalize_decision(recommendation.get("decision"))
    target_dte_bucket = normalize_target_dte_bucket(recommendation.get("target_dte_bucket"))
    explicit_expiration_filters_present = any(
        value
        for value in (
            option_expiration_date,
            option_expiration_date_gte,
            option_expiration_date_lte,
        )
    )

    refresh_debug = {
        "bucket_upstream_refresh_attempted": False,
        "bucket_upstream_refresh_applied": False,
        "bucket_upstream_refresh_skipped_reason": "",
        "bucket_upstream_refresh_filters": {},
        "bucket_upstream_contract_count": int(
            market_context.get("option_market", {}).get("contract_count") or 0
        ),
    }

    if decision not in {"call", "put"}:
        refresh_debug["bucket_upstream_refresh_skipped_reason"] = "decision_not_directional"
        return market_context, refresh_debug

    if not target_dte_bucket or target_dte_bucket == "none":
        refresh_debug["bucket_upstream_refresh_skipped_reason"] = "target_dte_bucket_none"
        return market_context, refresh_debug

    if explicit_expiration_filters_present:
        refresh_debug["bucket_upstream_refresh_skipped_reason"] = "explicit_expiration_filters_present"
        return market_context, refresh_debug

    translated_expiration_date, translated_expiration_date_gte, translated_expiration_date_lte = (
        target_dte_bucket_to_expiration_filters(target_dte_bucket)
    )
    refresh_debug["bucket_upstream_refresh_attempted"] = True
    refresh_debug["bucket_upstream_refresh_filters"] = {
        "expiration_date": translated_expiration_date or "",
        "expiration_date_gte": translated_expiration_date_gte or "",
        "expiration_date_lte": translated_expiration_date_lte or "",
    }

    refreshed_market_context = build_market_context(
        company,
        option_expiration_date=translated_expiration_date,
        option_expiration_date_gte=translated_expiration_date_gte,
        option_expiration_date_lte=translated_expiration_date_lte,
        option_strike_price_gte=option_strike_price_gte,
        option_strike_price_lte=option_strike_price_lte,
        option_contract_limit_per_type=option_contract_limit_per_type,
    )
    refresh_debug["bucket_upstream_refresh_applied"] = True
    refresh_debug["bucket_upstream_contract_count"] = int(
        refreshed_market_context.get("option_market", {}).get("contract_count") or 0
    )
    return refreshed_market_context, refresh_debug


def _build_no_evidence_result(
    company: dict[str, Any],
    *,
    context_snapshot: dict[str, Any],
    market_context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "company": company,
        "context_snapshot": context_snapshot,
        "market_context": market_context,
        "recommendation": {
            "decision": "neither",
            "confidence": "low",
            "target_dte_bucket": "none",
            "technical_confirmation": "weak",
            "entry_quality": "poor",
            "recommended_action_adjustment": "skip",
            "overextension_risk": "medium",
            "options_pricing_risk": "medium",
            "selected_option_id": None,
            "selected_expiration_date": None,
            "selected_strike_price": None,
            "selected_option_source": "manager_decision_only",
            "reason": "There was not enough processed article evidence or live market/account context available to support a call or put decision.",
            "technical_summary": "Technical context was not sufficient to confirm an actionable catalyst-driven setup.",
            "risk_summary": "Insufficient evidence and incomplete live context made timing and options risk unattractive.",
            "selection_debug": {
                "manager_module_path": __file__,
                "manager_stage_version": MANAGER_STAGE_VERSION,
                "option_contract_count": len(market_context.get("option_market", {}).get("contracts", [])),
            },
        },
        "selected_option": None,
    }


def replay_manager_decision_from_payload(
    payload: dict[str, Any],
    *,
    client: Client | None = None,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    """Re-run only the manager LLM against a frozen decision-time payload.

    This intentionally does not refresh market data, select an option contract,
    submit an order, or write an execution record.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("company"), dict):
        raise ValueError("A frozen manager replay payload with company context is required.")

    replay_payload = copy.deepcopy(payload)
    if not payload_has_evidence(replay_payload):
        return {
            "recommendation": {
                "decision": "neither",
                "confidence": "low",
                "target_dte_bucket": "none",
                "reason": "Frozen manager payload did not contain enough evidence for a directional replay.",
            },
            "replay_skipped": "no_evidence",
        }

    system_prompt, user_prompt = build_manager_prompt(replay_payload)
    raw_response = ask_model(
        client=client or get_default_client(),
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    parsed = extract_json_value(raw_response)
    recommendation = normalize_recommendation(
        extract_recommendation(parsed),
        strategist_recommendation=replay_payload.get("strategist_recommendation", {}),
    )
    if recommendation is None:
        recommendation = normalize_recommendation(
            extract_recommendation_from_text(raw_response),
            strategist_recommendation=replay_payload.get("strategist_recommendation", {}),
        )
    if recommendation is None:
        raise RuntimeError("Manager replay model returned an invalid recommendation response.")
    return {"recommendation": recommendation, "replay_prompt_version": MANAGER_PROMPT_VERSION}


def decide_company_option_position(
    company_identifier: str,
    *,
    client: Client | None = None,
    model: str = DEFAULT_MODEL,
    strategist_recommendation: dict[str, Any] | None = None,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
    summary_article_limit: int = DEFAULT_SUMMARY_ARTICLE_LIMIT,
    full_article_limit: int = DEFAULT_FULL_ARTICLE_LIMIT,
    option_expiration_date: str | None = None,
    option_expiration_date_gte: str | None = None,
    option_expiration_date_lte: str | None = None,
    option_strike_price_gte: float | None = None,
    option_strike_price_lte: float | None = None,
    option_contract_limit_per_type: int = DEFAULT_OPTION_CHAIN_LIMIT_PER_TYPE,
) -> dict[str, Any]:
    client = client or get_default_client()
    payload = build_manager_input(
        company_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
        summary_article_limit=summary_article_limit,
        full_article_limit=full_article_limit,
        option_expiration_date=option_expiration_date,
        option_expiration_date_gte=option_expiration_date_gte,
        option_expiration_date_lte=option_expiration_date_lte,
        option_strike_price_gte=option_strike_price_gte,
        option_strike_price_lte=option_strike_price_lte,
        option_contract_limit_per_type=option_contract_limit_per_type,
    )
    payload["strategist_recommendation"] = strategist_recommendation or {}
    payload["strategist_recommendation"] = _build_manager_visible_strategist_context(payload)

    company = payload["company"]
    market_context = payload.get("market_context", {})
    option_contracts = market_context.get("option_market", {}).get("contracts", [])
    context_snapshot = _build_context_snapshot(payload)

    if not payload_has_evidence(payload):
        return {
            **_build_no_evidence_result(
            company,
            context_snapshot=context_snapshot,
            market_context=market_context,
            ),
            "resolved_autopsy_insights": payload.get("resolved_autopsy_insights", []),
            "manager_replay_payload": copy.deepcopy(payload),
            "manager_model": model,
            "manager_prompt_version": MANAGER_PROMPT_VERSION,
        }

    system_prompt, user_prompt = build_manager_prompt(
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
    recommendation = normalize_recommendation(
        extract_recommendation(parsed),
        strategist_recommendation=payload.get("strategist_recommendation", {}),
    )
    if recommendation is None:
        recommendation = normalize_recommendation(
            extract_recommendation_from_text(raw_response),
            strategist_recommendation=payload.get("strategist_recommendation", {}),
        )
    if recommendation is None:
        raise RuntimeError(
            "Manager model returned an invalid response. "
            "Expected a JSON object with recommendation.decision/confidence/reason. "
            f"Raw response: {raw_response[:800]}"
        )

    market_context, upstream_bucket_refresh_debug = _refresh_market_context_for_target_dte_bucket(
        company=company,
        market_context=market_context,
        recommendation=recommendation,
        option_expiration_date=option_expiration_date,
        option_expiration_date_gte=option_expiration_date_gte,
        option_expiration_date_lte=option_expiration_date_lte,
        option_strike_price_gte=option_strike_price_gte,
        option_strike_price_lte=option_strike_price_lte,
        option_contract_limit_per_type=option_contract_limit_per_type,
    )
    option_contracts = market_context.get("option_market", {}).get("contracts", [])
    context_snapshot = {
        **context_snapshot,
        "option_contract_count": len(option_contracts),
    }

    recommendation = {
        **recommendation,
        "selection_debug": {
            "manager_module_path": __file__,
            "manager_stage_version": MANAGER_STAGE_VERSION,
            "option_contract_count": len(option_contracts),
            **upstream_bucket_refresh_debug,
        },
    }
    LOGGER.info(
        "Manager decision for %s produced decision=%s confidence=%s with %s contracts available",
        company.get("symbol"),
        recommendation.get("decision"),
        recommendation.get("confidence"),
        len(option_contracts),
    )

    return {
        "company": company,
        "context_snapshot": context_snapshot,
        "market_context": market_context,
        "supporting_articles": payload.get("supporting_articles", {}),
        "recent_manager_decision_history": payload.get("recent_manager_decision_history", []),
        "resolved_autopsy_insights": payload.get("resolved_autopsy_insights", []),
        "strategist_recommendation": payload.get("strategist_recommendation", {}),
        "recommendation": recommendation,
        "selected_option": None,
        "manager_replay_payload": copy.deepcopy(payload),
        "manager_model": model,
        "manager_prompt_version": MANAGER_PROMPT_VERSION,
    }
