from __future__ import annotations

import copy
from typing import Any

from .manager_shared import (
    VALID_QUALITY_LEVELS,
    VALID_STRATEGIST_DECISIONS,
    VALID_TIMING_CLARITY,
    coerce_bool,
    normalize_confidence,
    normalize_decision,
    normalize_time_horizon,
)


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


def _normalize_strategist_decision(value: Any) -> str:
    decision = str(value or "").strip().lower()
    replacements = {
        "buy": "trade_candidate",
        "candidate": "trade_candidate",
        "trade": "trade_candidate",
        "trade candidate": "trade_candidate",
        "trade_candidate": "trade_candidate",
        "watch": "watchlist",
        "watch list": "watchlist",
        "watchlist": "watchlist",
        "hold_for_now": "watchlist",
        "hold for now": "watchlist",
        "do_not_trade": "do_not_trade",
        "do not trade": "do_not_trade",
        "avoid": "do_not_trade",
        "pass": "do_not_trade",
        "hold": "do_not_trade",
    }
    decision = replacements.get(decision, decision)
    return decision if decision in VALID_STRATEGIST_DECISIONS else ""


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


def _normalize_strategist_recommendation(recommendation: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(recommendation, dict):
        return {}

    decision = _normalize_strategist_decision(recommendation.get("decision"))
    confidence = normalize_confidence(recommendation.get("confidence"))
    evidence_quality = _normalize_quality(recommendation.get("evidence_quality"))
    setup_quality = _normalize_quality(recommendation.get("setup_quality"))
    timing_clarity = _normalize_timing_clarity(recommendation.get("timing_clarity"))
    preferred_option_direction = normalize_decision(
        recommendation.get("preferred_option_direction")
        or recommendation.get("option_direction")
        or recommendation.get("options_bias")
        or recommendation.get("option_bias")
    )
    expected_stock_direction = str(
        recommendation.get("expected_stock_direction")
        or recommendation.get("stock_direction")
        or recommendation.get("direction")
        or recommendation.get("price_direction")
        or ""
    ).strip().lower()
    stock_direction_replacements = {
        "bullish": "up",
        "bearish": "down",
        "flat": "neutral",
        "sideways": "neutral",
    }
    expected_stock_direction = stock_direction_replacements.get(
        expected_stock_direction,
        expected_stock_direction,
    )
    if expected_stock_direction not in {"up", "down", "neutral"}:
        expected_stock_direction = ""

    time_horizon = normalize_time_horizon(recommendation.get("time_horizon"))
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
    contradictions_present = coerce_bool(recommendation.get("contradictions_present"))
    watchlist_reason = str(recommendation.get("watchlist_reason") or "").strip()

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
        preferred_option_direction = {"up": "call", "down": "put", "neutral": "neither"}.get(
            expected_stock_direction,
            "",
        )
    if not expected_stock_direction and preferred_option_direction:
        expected_stock_direction = {"call": "up", "put": "down", "neither": "neutral"}.get(
            preferred_option_direction,
            "",
        )

    if decision == "do_not_trade":
        preferred_option_direction = preferred_option_direction or "neither"
        expected_stock_direction = expected_stock_direction or "neutral"
    if decision == "watchlist" and not preferred_option_direction:
        preferred_option_direction = "neither"
    if decision == "watchlist" and not expected_stock_direction:
        expected_stock_direction = "neutral"

    normalized = {
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
    return {key: value for key, value in normalized.items() if value not in ("", [], None)}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _valid_grounded_fields(recommendation: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    grounding = payload.get("grounding") if isinstance(payload.get("grounding"), dict) else {}
    company = payload.get("company") if isinstance(payload.get("company"), dict) else {}
    expected_symbol = str(
        grounding.get("company", {}).get("symbol") or company.get("symbol") or ""
    ).strip().upper()
    response_symbol = str(recommendation.get("grounded_company_symbol") or "").strip().upper()
    if not expected_symbol or response_symbol != expected_symbol:
        return {}

    entity_ids = recommendation.get("grounded_entity_ids")
    if not isinstance(entity_ids, dict):
        return {}
    normalized_entity_ids: dict[str, int] = {}
    for entity_name, response_key in (
        ("company", "company_id"),
        ("industry", "industry_id"),
        ("sector", "sector_id"),
    ):
        expected_id = _positive_int(grounding.get(entity_name, {}).get("id"))
        if expected_id is None:
            expected_id = _positive_int(company.get(response_key))
        response_id = _positive_int(entity_ids.get(response_key))
        if expected_id is not None and response_id != expected_id:
            return {}
        if response_id is not None:
            normalized_entity_ids[response_key] = response_id
    if not normalized_entity_ids:
        return {}

    raw_allowed_article_ids = grounding.get("article_ids", [])
    if not isinstance(raw_allowed_article_ids, list):
        return {}
    allowed_article_ids = {
        article_id
        for article_id in (_positive_int(value) for value in raw_allowed_article_ids)
        if article_id is not None
    }
    claims = recommendation.get("evidence_claims")
    if not isinstance(claims, list):
        return {}
    normalized_claims: list[dict[str, Any]] = []
    valid_claim_types = {"summary", "why_now", "catalyst", "thesis", "risk", "watchlist_reason"}
    for claim in claims:
        if not isinstance(claim, dict):
            return {}
        claim_type = str(claim.get("claim_type") or "").strip().lower()
        claim_text = str(claim.get("claim") or "").strip()
        raw_article_ids = claim.get("evidence_article_ids")
        if claim_type not in valid_claim_types or not claim_text or not isinstance(raw_article_ids, list):
            return {}
        article_ids = [_positive_int(article_id) for article_id in raw_article_ids]
        if not article_ids or any(article_id is None or article_id not in allowed_article_ids for article_id in article_ids):
            return {}
        normalized_claims.append(
            {
                "claim_type": claim_type,
                "claim": claim_text,
                "evidence_article_ids": article_ids,
            }
        )

    result: dict[str, Any] = {
        "grounded_company_symbol": response_symbol,
        "grounded_entity_ids": normalized_entity_ids,
        "evidence_claims": normalized_claims,
    }
    run_reference = recommendation.get("strategist_run_reference")
    if isinstance(run_reference, dict):
        run_id = _positive_int(run_reference.get("id"))
        raw_input_article_ids = run_reference.get("input_article_ids", [])
        if not isinstance(raw_input_article_ids, list):
            return result
        input_article_ids = [_positive_int(value) for value in raw_input_article_ids]
        model = str(run_reference.get("model") or "").strip()
        prompt_version = str(run_reference.get("prompt_version") or "").strip()
        created_at = str(run_reference.get("created_at") or "").strip()
        if (
            run_id is not None
            and model
            and prompt_version
            and created_at
            and all(article_id is not None and article_id in allowed_article_ids for article_id in input_article_ids)
        ):
            result["strategist_run_reference"] = {
                "id": run_id,
                "model": model,
                "prompt_version": prompt_version,
                "input_article_ids": input_article_ids,
                "created_at": created_at,
            }
    return result


def _evidence_thesis_fields(recommendation: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Pass the strategist's checked thesis through unchanged, or flag why it cannot be.

    The thesis is only meaningful against the exact package it was checked with, so a
    package that differs from the manager's frozen company_evidence is never shown.
    """
    check = recommendation.get("evidence_thesis_check")
    if not isinstance(check, dict):
        # Idempotent: the prompt builder re-projects an already projected context.
        mismatch = recommendation.get("evidence_thesis_handoff") == "package_mismatch"
        return {"evidence_thesis_handoff": "package_mismatch"} if mismatch else {}
    evidence = payload.get("company_evidence") if isinstance(payload.get("company_evidence"), dict) else {}
    if check.get("package_id") != evidence.get("package_id"):
        return {"evidence_thesis_handoff": "package_mismatch"}
    return {
        "evidence_thesis": copy.deepcopy(recommendation.get("evidence_thesis")),
        "evidence_thesis_check": copy.deepcopy(check),
        "evidence_thesis_handoff": "same_package",
    }


def _build_manager_visible_strategist_context(payload: dict[str, Any]) -> dict[str, Any]:
    recommendation = payload.get("strategist_recommendation", {})
    normalized = _normalize_strategist_recommendation(recommendation)
    if not isinstance(recommendation, dict):
        return normalized
    return {**normalized, **_valid_grounded_fields(recommendation, payload),
            **_evidence_thesis_fields(recommendation, payload)}
