from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from agent_helpers.manager_inputs import (
    DEFAULT_FULL_ARTICLE_LIMIT,
    DEFAULT_MAX_ARTICLE_AGE_DAYS,
    DEFAULT_SUMMARY_ARTICLE_LIMIT,
    build_manager_input,
    build_market_context,
)
from agent_contracts import ManagerResult
from agent_helpers.llm_client import Client, build_model_response_diagnostic
from agent_helpers.text_utils import extract_json_value
from services.evidence_continuation_gate import evaluate_evidence_continuation_gate
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
from services.settings import get_settings
from .manager_model import (
    LOGGER,
    MANAGER_PROMPT_VERSION,
    MANAGER_STAGE_VERSION,
    ask_model,
    get_default_client,
    normalize_decision,
    normalize_target_dte_bucket,
    target_dte_bucket_to_expiration_filters,
)


def _apply_manager_evidence_payload(
    payload: dict[str, Any],
    manager_evidence_payload: dict[str, Any] | None,
) -> bool:
    """Use the strategist's frozen, company-matched evidence in this decision.

    The manager keeps fresh market context, but its evidence gate must evaluate
    the same articles and scores that the strategist cited.
    """
    if not isinstance(manager_evidence_payload, dict):
        return False

    company = payload.get("company") if isinstance(payload.get("company"), dict) else {}
    evidence_company = manager_evidence_payload.get("company")
    evidence_company = evidence_company if isinstance(evidence_company, dict) else {}
    expected_symbol = str(company.get("symbol") or "").strip().upper()
    evidence_symbol = str(evidence_company.get("symbol") or "").strip().upper()
    if not expected_symbol or evidence_symbol != expected_symbol:
        LOGGER.warning("Ignoring manager evidence payload with mismatched company symbol.")
        return False

    supporting_articles = manager_evidence_payload.get("supporting_articles")
    grounding = manager_evidence_payload.get("grounding")
    if not isinstance(supporting_articles, dict) or not isinstance(grounding, dict):
        return False

    for key in ("filters", "opportunist_rollup", "views", "supporting_articles", "grounding", "company_evidence"):
        value = manager_evidence_payload.get(key)
        if value is not None:
            payload[key] = copy.deepcopy(value)
    return True


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
        option_contract_limit_per_type=max(
            option_contract_limit_per_type,
            get_settings().manager_context.option_selection_limit_per_type,
        ),
    )
    if isinstance(market_context.get("market_regime"), dict):
        refreshed_market_context["market_regime"] = market_context["market_regime"]
    refresh_debug["bucket_upstream_refresh_applied"] = True
    refresh_debug["bucket_upstream_contract_count"] = int(
        refreshed_market_context.get("option_market", {}).get("contract_count") or 0
    )
    return refreshed_market_context, refresh_debug


_NO_EVIDENCE_RECOMMENDATION: dict[str, Any] = {
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
}


# The strategist's no is final: the manager only decides how to act on a trade_candidate.
STRATEGIST_DECLINES = {"watchlist", "do_not_trade"}


def _strategist_declined_recommendation(decision: str) -> dict[str, Any]:
    return {**copy.deepcopy(_NO_EVIDENCE_RECOMMENDATION), "selected_option_source": "strategist_declined",
            "reason": f"The strategist answered {decision}; the manager was not asked.",
            "technical_summary": "Not assessed: the strategist declined the company.",
            "risk_summary": "Not assessed: the strategist declined the company."}


def _build_no_evidence_result(
    company: dict[str, Any],
    *,
    recommendation: dict[str, Any],
    context_snapshot: dict[str, Any],
    market_context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "company": company,
        "context_snapshot": context_snapshot,
        "market_context": market_context,
        "recommendation": {
            **recommendation,
            "selection_debug": {
                "manager_module_path": __file__,
                "manager_stage_version": MANAGER_STAGE_VERSION,
                "option_contract_count": len(market_context.get("option_market", {}).get("contracts", [])),
            },
        },
        "selected_option": None,
    }


def decide_manager_recommendation(
    payload: dict[str, Any],
    *,
    client: Client,
    model: str,
    candidate_context: dict[str, Any] | None = None,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> dict[str, Any]:
    """The manager's decision on a finished payload, shared by live runs and replays.

    Returns the normalized recommendation and `skipped`: the short circuit that answered
    instead of the model ("no_evidence" or "strategist_declined"), or None.
    """
    if not payload_has_evidence(payload):
        return {"recommendation": copy.deepcopy(_NO_EVIDENCE_RECOMMENDATION), "skipped": "no_evidence"}
    strategist_decision = (payload.get("strategist_recommendation") or {}).get("decision")
    if strategist_decision in STRATEGIST_DECLINES:
        return {"recommendation": _strategist_declined_recommendation(strategist_decision),
                "skipped": "strategist_declined"}

    system_prompt, user_prompt = build_manager_prompt(
        payload,
        system_prompt_override=system_prompt_override,
        task_override=task_override,
    )
    response_diagnostic: dict[str, Any] = {}
    from services.candidate_lifecycle import checkpoint
    checkpoint(candidate_context, "manager_prompt", {"model": model,
               "system_prompt": system_prompt, "user_prompt": user_prompt})
    raw_response = ask_model(
        client=client,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_diagnostic=response_diagnostic,
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
        company = payload.get("company") if isinstance(payload.get("company"), dict) else {}
        diagnostic = build_model_response_diagnostic(
            raw_response,
            response_diagnostic=response_diagnostic,
        )
        diagnostic["stage"] = "manager"
        diagnostic["company_symbol"] = str(company.get("symbol") or "").strip().upper()
        LOGGER.error("Manager response diagnostic: %s", diagnostic)
        raise RuntimeError(
            "Manager model returned an invalid response. "
            "Expected a JSON object with recommendation.decision/confidence/reason. "
            "See the preceding manager response diagnostic for the parse error and completion metadata."
        )
    return {"recommendation": recommendation, "skipped": None, "response_diagnostic": response_diagnostic}


def replay_manager_decision_from_payload(
    payload: dict[str, Any],
    *,
    client: Client | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Re-run only the manager LLM against a frozen decision-time payload.

    This intentionally does not refresh market data, select an option contract,
    submit an order, or write an execution record.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("company"), dict):
        raise ValueError("A frozen manager replay payload with company context is required.")
    decision = decide_manager_recommendation(
        copy.deepcopy(payload),
        client=client or get_default_client(),
        model=model or get_settings().models.manager.model,
    )
    result = {"recommendation": decision["recommendation"], "replay_prompt_version": MANAGER_PROMPT_VERSION}
    if decision["skipped"]:
        result["replay_skipped"] = decision["skipped"]
    return result


def decide_company_option_position(
    company_identifier: str,
    *,
    client: Client | None = None,
    model: str | None = None,
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
    option_contract_limit_per_type: int | None = None,
    market_regime: dict[str, Any] | None = None,
    manager_evidence_payload: dict[str, Any] | None = None,
    candidate_context: dict[str, Any] | None = None,
) -> ManagerResult:
    if option_contract_limit_per_type is None:
        option_contract_limit_per_type = get_settings().manager_context.option_chain_limit_per_type
    client = client or get_default_client()
    model = model or get_settings().models.manager.model
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
    evidence_payload_applied = _apply_manager_evidence_payload(payload, manager_evidence_payload)
    if isinstance(market_regime, dict):
        payload.setdefault("market_context", {})["market_regime"] = market_regime
    payload["strategist_recommendation"] = strategist_recommendation or {}
    payload["strategist_recommendation"] = _build_manager_visible_strategist_context(payload)

    company = payload["company"]
    market_context = payload.get("market_context", {})
    option_contracts = market_context.get("option_market", {}).get("contracts", [])
    context_snapshot = _build_context_snapshot(payload)
    context_snapshot["strategist_evidence_payload_applied"] = evidence_payload_applied
    from services.candidate_lifecycle import checkpoint
    checkpoint(candidate_context, "pre_manager", {"payload": payload, "model": model})

    decision = decide_manager_recommendation(
        payload,
        client=client,
        model=model,
        candidate_context=candidate_context,
        system_prompt_override=system_prompt_override,
        task_override=task_override,
    )
    recommendation = decision["recommendation"]

    if decision["skipped"]:
        no_evidence_result = _build_no_evidence_result(
            company,
            recommendation=recommendation,
            context_snapshot=context_snapshot,
            market_context=market_context,
        )
        checkpoint(candidate_context, "pre_gates", {"payload": payload,
                   "recommendation": no_evidence_result["recommendation"], "market_context": market_context})
        return {
            **no_evidence_result,
            # Same context as an answered result: history, the dashboard and later gates read it either way.
            "supporting_articles": payload.get("supporting_articles", {}),
            "company_evidence": payload.get("company_evidence", {}),
            "recent_manager_decision_history": payload.get("recent_manager_decision_history", []),
            "strategist_recommendation": payload.get("strategist_recommendation", {}),
            "entry_gate": evaluate_evidence_continuation_gate(
                recommendation=no_evidence_result["recommendation"],
                payload=payload,
                market_context=market_context,
            ),
            "resolved_autopsy_insights": payload.get("resolved_autopsy_insights", []),
            "manager_replay_payload": copy.deepcopy(payload),
            "manager_model": model,
            "manager_prompt_version": MANAGER_PROMPT_VERSION,
            "market_regime": market_context.get("market_regime", {}),
            "response_diagnostic": decision.get("response_diagnostic", {}),
        }

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
    from services.candidate_lifecycle import checkpoint
    checkpoint(candidate_context, "pre_gates", {"payload": payload, "recommendation": recommendation,
                                                "market_context": market_context})
    entry_gate = evaluate_evidence_continuation_gate(
        recommendation=recommendation,
        payload=payload,
        market_context=market_context,
    )
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
        "company_evidence": payload.get("company_evidence", {}),
        "recent_manager_decision_history": payload.get("recent_manager_decision_history", []),
        "resolved_autopsy_insights": payload.get("resolved_autopsy_insights", []),
        "strategist_recommendation": payload.get("strategist_recommendation", {}),
        "recommendation": recommendation,
        "entry_gate": entry_gate,
        "selected_option": None,
        "manager_replay_payload": copy.deepcopy(payload),
        "manager_model": model,
        "manager_prompt_version": MANAGER_PROMPT_VERSION,
        "market_regime": market_context.get("market_regime", {}),
        "response_diagnostic": decision.get("response_diagnostic", {}),
    }
