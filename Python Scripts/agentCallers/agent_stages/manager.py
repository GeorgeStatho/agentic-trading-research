from __future__ import annotations

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any

if __package__ in {None, ""}:
    AGENT_CALLERS_DIR = Path(__file__).resolve().parents[1]
    if str(AGENT_CALLERS_DIR) not in sys.path:
        sys.path.append(str(AGENT_CALLERS_DIR))

from _paths import bootstrap_agent_callers


bootstrap_agent_callers(load_env_file=True)
from agent_helpers.manager import (
    DEFAULT_FULL_ARTICLE_LIMIT,
    DEFAULT_MAX_ARTICLE_AGE_DAYS,
    DEFAULT_OPTION_CHAIN_LIMIT_PER_TYPE,
    DEFAULT_SUMMARY_ARTICLE_LIMIT,
    build_manager_input,
)
from _shared import Client, ask_llm_model, extract_json_value, get_model_client


MODEL_BACKEND_LABEL = os.getenv(
    "MANAGER_OLLAMA_HOST",
    os.getenv("MACRO_NEWS_OLLAMA_HOST", os.getenv("WORLD_NEWS_OLLAMA_HOST", "http://127.0.0.1:11434")),
)
DEFAULT_MODEL = os.getenv(
    "MANAGER_MODEL",
    os.getenv("MACRO_NEWS_MODEL", os.getenv("WORLD_NEWS_MODEL", "world-news-sectors")),
)
MANAGER_STAGE_VERSION = "decision-only-v3"

VALID_DECISIONS = {"call", "put", "neither"}
VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}
VALID_TARGET_DTE_BUCKETS = {"3_7", "7_14", "14_30", "none"}
VALID_STRATEGIST_DECISIONS = {"trade_candidate", "watchlist", "do_not_trade"}
VALID_QUALITY_LEVELS = {"strong", "moderate", "weak"}
VALID_TIMING_CLARITY = {"clear", "unclear"}
VALID_TIME_HORIZONS = {"very_short_term", "short_term", "medium_term", "unclear"}

_manager_client: Client | None = None
LOGGER = logging.getLogger(__name__)
MANAGER_RECOMMENDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recommendation": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["call", "put", "neither"]},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "target_dte_bucket": {"type": "string", "enum": ["3_7", "7_14", "14_30", "none"]},
                "reason": {"type": "string"},
            },
            "required": ["decision", "confidence", "target_dte_bucket", "reason"],
            "additionalProperties": False,
        }
    },
    "required": ["recommendation"],
    "additionalProperties": False,
}

__all__ = [
    "build_manager_prompt",
    "decide_company_option_position",
]


def _get_default_client() -> Client:
    global _manager_client
    if _manager_client is None:
        _manager_client = get_model_client(MODEL_BACKEND_LABEL)
    return _manager_client


def ask_model(client: Client, model: str, system_prompt: str, user_prompt: str) -> str:
    return ask_llm_model(
        client,
        model,
        system_prompt,
        user_prompt,
        temperature=0,
        host_label=MODEL_BACKEND_LABEL,
        response_schema=MANAGER_RECOMMENDATION_SCHEMA,
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

    market_context = payload.get("market_context", {})
    stock_price_available = bool(market_context.get("current_stock_price", {}).get("available"))
    option_contract_count = int(market_context.get("option_market", {}).get("contract_count") or 0)
    account_state_available = bool(market_context.get("account_state", {}).get("available"))

    return (
        view_total > 0
        or bool(article_summaries)
        or bool(full_articles)
        or stock_price_available
        or option_contract_count > 0
        or account_state_available
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


def _normalize_strategist_recommendation(recommendation: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(recommendation, dict):
        return {}

    decision = _normalize_strategist_decision(recommendation.get("decision"))
    confidence = _normalize_confidence(recommendation.get("confidence"))
    evidence_quality = _normalize_quality(recommendation.get("evidence_quality"))
    setup_quality = _normalize_quality(recommendation.get("setup_quality"))
    timing_clarity = _normalize_timing_clarity(recommendation.get("timing_clarity"))
    preferred_option_direction = _normalize_decision(
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

    if decision and decision not in VALID_STRATEGIST_DECISIONS:
        decision = ""
    if confidence and confidence not in VALID_CONFIDENCE_LEVELS:
        confidence = ""
    if preferred_option_direction and preferred_option_direction not in VALID_DECISIONS:
        preferred_option_direction = ""
    if expected_stock_direction and expected_stock_direction not in {"up", "down", "neutral"}:
        expected_stock_direction = ""

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


def _build_manager_visible_strategist_context(payload: dict[str, Any]) -> dict[str, Any]:
    return _normalize_strategist_recommendation(payload.get("strategist_recommendation", {}))


def _build_context_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    market_context = payload.get("market_context", {})
    strategist_recommendation = _build_manager_visible_strategist_context(payload)
    market_indices = market_context.get("market_indices", {})
    return {
        "view_counts": {
            key: int(value.get("count") or 0)
            for key, value in payload.get("views", {}).items()
            if isinstance(value, dict)
        },
        "article_summary_count": len(payload.get("supporting_articles", {}).get("article_summaries", [])),
        "full_article_count": len(payload.get("supporting_articles", {}).get("full_articles", [])),
        "stock_price_available": bool(market_context.get("current_stock_price", {}).get("available")),
        "market_index_count_available": sum(
            1
            for snapshot in market_indices.values()
            if isinstance(snapshot, dict) and snapshot.get("available")
        ),
        "option_contract_count": int(market_context.get("option_market", {}).get("contract_count") or 0),
        "account_state_available": bool(market_context.get("account_state", {}).get("available")),
        "matching_position_count": int(
            market_context.get("account_state", {})
            .get("company_position_state", {})
            .get("matching_position_count")
            or 0
        ),
        "strategist_decision": str(strategist_recommendation.get("decision") or ""),
        "strategist_option_direction": str(
            strategist_recommendation.get("preferred_option_direction") or ""
        ),
        "strategist_stock_direction": str(
            strategist_recommendation.get("expected_stock_direction") or ""
        ),
        "strategist_evidence_quality": str(strategist_recommendation.get("evidence_quality") or ""),
        "strategist_setup_quality": str(strategist_recommendation.get("setup_quality") or ""),
        "strategist_timing_clarity": str(strategist_recommendation.get("timing_clarity") or ""),
        "strategist_time_horizon": str(strategist_recommendation.get("time_horizon") or ""),
        "strategist_contradictions_present": bool(
            strategist_recommendation.get("contradictions_present")
        ),
    }


def _build_manager_visible_market_context(payload: dict[str, Any]) -> dict[str, Any]:
    market_context = payload.get("market_context", {})
    option_market = market_context.get("option_market", {})

    return {
        "current_stock_price": market_context.get("current_stock_price", {}),
        "market_indices": market_context.get("market_indices", {}),
        "account_state": market_context.get("account_state", {}),
        "option_market_summary": {
            "available": bool(option_market.get("available")),
            "underlying_symbol": option_market.get("underlying_symbol"),
            "selection_filters": option_market.get("selection_filters", {}),
            "contract_count": int(option_market.get("contract_count") or 0),
            "available_expirations": option_market.get("available_expirations", []),
            "available_strikes": option_market.get("available_strikes", []),
            "error": str(option_market.get("error") or ""),
        },
    }


def build_manager_prompt(
    payload: dict[str, Any],
    *,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> tuple[str, str]:
    """Build the manager stage prompt pair from a prepared payload.

    Usage:
        Pass the result of ``agent_helpers.manager.build_manager_input(...)``.
    """
    default_system_prompt = (
        "You are an investment manager deciding whether a company currently supports a bullish options call, "
    "a bearish options put, or neither. "
    "Use only the supplied structured context. "
    "You are reviewing: "
    "1) the structured research package, including article evidence and 1d, 5d, 1mo, and 3mo price history; "
    "2) live market and account context, including current stock price, option market summary, buying power, and current position state; "
    "and 3) the strategist recommendation as an upstream signal. "
    "Treat the strategist recommendation as useful but not authoritative. "
    "The strategist may include decision, confidence, evidence_quality, setup_quality, timing_clarity, "
    "preferred_option_direction, expected_stock_direction, time_horizon, why_now, thesis, risks, and contradictions_present. "
    "Use that information as one input, but make your own final call from the full combined context. "
    "Your job is only to choose call, put, or neither for the underlying at this time. "
    "Do not choose, rank, or infer a specific option contract. "
    "Choose 'call' only when the evidence is convincingly bullish, timing is sufficiently actionable for options, "
    "and account/market context can support opening the trade. Choose 'call' even if the market context is only trending downwards for the day due to volatility and prefer evidence more if is overly convincingly bullish"
    "Choose 'put' only when the evidence is convincingly bearish, timing is sufficiently actionable for options, "
    "and account/market context can support opening the trade. Choose 'put' even if the market context is only trending upwards for the day due to volatility and prefer evidence more if is overly convincingly bearish"
    "Prefer 'neither' when evidence is weak, stale, contradictory, directionally unclear, timing is unclear, "
    "setup quality is weak, or account constraints make the trade unattractive. "
    "If the strategist says watchlist, weak setup, unclear timing, or contradictions_present=true, that should usually push toward 'neither' "
    "unless the full context strongly supports immediate action anyway. "
        "Return only valid JSON with a top-level key named 'recommendation'. "
        "The recommendation object must contain: decision, confidence, target_dte_bucket, reason. "
        "decision must be one of: call, put, neither. "
        "confidence must be one of: high, medium, low. "
        "target_dte_bucket must be one of: 3_7, 7_14, 14_30, none. "
        "Use 'none' when no options trade should be opened or when no DTE preference should be expressed. "
        "reason must be a short paragraph explaining the main drivers of the decision."
    )
    system_prompt = str(system_prompt_override or default_system_prompt)

    user_payload = {
        "task": str(task_override or "Decide whether the supplied company currently supports a call, put, or neither."),
        "company": payload["company"],
        "peer_groups": payload.get("peer_groups", {}),
        "filters": payload.get("filters", {}),
        "views": payload.get("views", {}),
        "supporting_articles": payload.get("supporting_articles", {}),
        "strategist_recommendation": _build_manager_visible_strategist_context(payload),
        "market_context": _build_manager_visible_market_context(payload),
        "required_output": {
            "recommendation": {
                "decision": "call|put|neither",
                "confidence": "high|medium|low",
                "target_dte_bucket": "3_7|7_14|14_30|none",
                "reason": "short paragraph",
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

    if any(key in payload for key in ("decision", "confidence", "target_dte_bucket", "reason")):
        return payload

    for key in ("output_schema", "required_output", "result", "analysis"):
        nested = payload.get(key)
        if not isinstance(nested, dict):
            continue

        nested_recommendation = nested.get("recommendation")
        if isinstance(nested_recommendation, dict):
            return nested_recommendation

        if any(inner_key in nested for inner_key in ("decision", "confidence", "target_dte_bucket", "reason")):
            return nested

    return None


def _normalize_decision(value: Any) -> str:
    decision = str(value or "").strip().lower()
    replacements = {
        "bullish": "call",
        "buy_call": "call",
        "buy call": "call",
        "calls": "call",
        "bearish": "put",
        "buy_put": "put",
        "buy put": "put",
        "puts": "put",
        "hold": "neither",
        "avoid": "neither",
        "pass": "neither",
        "no_trade": "neither",
        "no trade": "neither",
        "do nothing": "neither",
        "neutral": "neither",
    }
    decision = replacements.get(decision, decision)
    return decision if decision in VALID_DECISIONS else ""


def _normalize_confidence(value: Any) -> str:
    confidence = str(value or "").strip().lower()
    replacements = {
        "strong": "high",
        "very_high": "high",
        "very high": "high",
        "moderate": "medium",
        "uncertain": "low",
        "weak": "low",
    }
    confidence = replacements.get(confidence, confidence)
    return confidence if confidence in VALID_CONFIDENCE_LEVELS else ""


def _normalize_target_dte_bucket(value: Any) -> str:
    bucket = str(value or "").strip().lower()
    replacements = {
        "3-7": "3_7",
        "3 to 7": "3_7",
        "7-14": "7_14",
        "7 to 14": "7_14",
        "14-30": "14_30",
        "14 to 30": "14_30",
        "n/a": "none",
        "na": "none",
        "not_applicable": "none",
    }
    bucket = replacements.get(bucket, bucket)
    return bucket if bucket in VALID_TARGET_DTE_BUCKETS else ""


def _normalize_option_id(value: Any) -> int | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value if value > 0 else None

    text = str(value or "").strip()
    if not text:
        return None

    try:
        normalized = int(text)
    except ValueError:
        return None

    return normalized if normalized > 0 else None


def _coerce_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_option_contract_maps(option_contracts: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], dict[str, int]]:
    contracts_by_id: dict[int, dict[str, Any]] = {}
    option_id_by_symbol: dict[str, int] = {}

    for contract in option_contracts:
        option_id = _normalize_option_id(contract.get("option_id"))
        if option_id is not None:
            contracts_by_id[option_id] = contract

        symbol = str(contract.get("symbol") or "").strip().upper()
        if symbol and option_id is not None:
            option_id_by_symbol[symbol] = option_id

    return contracts_by_id, option_id_by_symbol


def _extract_model_selected_option_id(
    recommendation: dict[str, Any],
    option_contracts: list[dict[str, Any]],
    contracts_by_id: dict[int, dict[str, Any]],
    option_id_by_symbol: dict[str, int],
) -> tuple[int | None, bool]:
    selected_option_id = _normalize_option_id(
        recommendation.get("selected_option_id")
        or recommendation.get("option_id")
        or recommendation.get("recommended_option_id")
    )
    if selected_option_id is not None and selected_option_id in contracts_by_id:
        return selected_option_id, True

    if selected_option_id is not None:
        selected_option_id_text = str(selected_option_id)
        for contract in option_contracts:
            contract_id = contract.get("contract_id")
            if str(contract_id or "").strip() == selected_option_id_text:
                resolved_option_id = _normalize_option_id(contract.get("option_id"))
                if resolved_option_id is not None:
                    return resolved_option_id, True

    selected_symbol = str(
        recommendation.get("selected_option_symbol")
        or recommendation.get("option_symbol")
        or recommendation.get("symbol")
        or ""
    ).strip().upper()
    if selected_symbol:
        resolved_option_id = option_id_by_symbol.get(selected_symbol)
        if resolved_option_id is not None:
            return resolved_option_id, True

    if selected_option_id is not None or selected_symbol:
        return None, True

    return None, False


def _get_reference_stock_price(market_context: dict[str, Any]) -> float | None:
    stock_snapshot = market_context.get("current_stock_price", {})
    for key in ("latest_trade_price", "price", "ask_price", "midpoint_price", "bid_price"):
        price = _coerce_float(stock_snapshot.get(key))
        if price is not None and price > 0:
            return price
    return None


def _get_contract_market_price(contract: dict[str, Any]) -> float | None:
    latest_quote = contract.get("latest_quote", {})
    if isinstance(latest_quote, dict):
        for key in ("midpoint_price", "ask_price", "bid_price"):
            price = _coerce_float(latest_quote.get(key))
            if price is not None and price > 0:
                return price

    for key in ("latest_trade_price", "close_price"):
        price = _coerce_float(contract.get(key))
        if price is not None and price > 0:
            return price

    return None


def _pick_fallback_option_id(
    *,
    decision: str,
    option_contracts: list[dict[str, Any]],
    market_context: dict[str, Any],
) -> int | None:
    matching_contracts = [
        contract
        for contract in option_contracts
        if _normalize_decision(contract.get("contract_type")) == decision
    ]
    if not matching_contracts:
        return None

    if len(matching_contracts) == 1:
        return _normalize_option_id(matching_contracts[0].get("option_id"))

    reference_stock_price = _get_reference_stock_price(market_context)

    def sort_key(contract: dict[str, Any]) -> tuple[float, float, str, float, int]:
        contract_price = _get_contract_market_price(contract)
        has_contract_price = 0.0 if contract_price is not None else 1.0

        strike_price = _coerce_float(contract.get("strike_price"))
        strike_distance = (
            abs(strike_price - reference_stock_price)
            if strike_price is not None and reference_stock_price is not None
            else float("inf")
        )

        expiration_date = str(contract.get("expiration_date") or "9999-12-31")
        open_interest = _coerce_float(contract.get("open_interest"))
        open_interest_rank = -(open_interest if open_interest is not None else -1.0)
        option_id = _normalize_option_id(contract.get("option_id")) or 10**9

        return (
            has_contract_price,
            strike_distance,
            expiration_date,
            open_interest_rank,
            option_id,
        )

    best_contract = min(matching_contracts, key=sort_key)
    return _normalize_option_id(best_contract.get("option_id"))


def _direct_pick_contract(
    *,
    decision: str,
    option_contracts: list[dict[str, Any]],
    market_context: dict[str, Any],
) -> dict[str, Any] | None:
    normalized_decision = str(decision or "").strip().lower()
    if normalized_decision not in {"call", "put"}:
        return None

    reference_stock_price = _get_reference_stock_price(market_context)
    best_contract: dict[str, Any] | None = None
    best_key: tuple[float, float, str, float, int] | None = None

    for contract in option_contracts:
        contract_type = str(contract.get("contract_type") or "").strip().lower()
        if contract_type != normalized_decision:
            continue

        contract_price = _get_contract_market_price(contract)
        has_contract_price = 0.0 if contract_price is not None else 1.0

        strike_price = _coerce_float(contract.get("strike_price"))
        strike_distance = (
            abs(strike_price - reference_stock_price)
            if strike_price is not None and reference_stock_price is not None
            else float("inf")
        )

        expiration_date = str(contract.get("expiration_date") or "9999-12-31")
        open_interest = _coerce_float(contract.get("open_interest"))
        open_interest_rank = -(open_interest if open_interest is not None else -1.0)
        option_id = _normalize_option_id(contract.get("option_id")) or 10**9

        candidate_key = (
            has_contract_price,
            strike_distance,
            expiration_date,
            open_interest_rank,
            option_id,
        )
        if best_key is None or candidate_key < best_key:
            best_key = candidate_key
            best_contract = contract

    return best_contract


def _normalize_recommendation(
    recommendation: dict[str, Any] | None,
    *,
    option_contracts: list[dict[str, Any]] | None = None,
    market_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(recommendation, dict):
        return None

    decision = _normalize_decision(recommendation.get("decision"))
    confidence = _normalize_confidence(recommendation.get("confidence"))
    target_dte_bucket = _normalize_target_dte_bucket(recommendation.get("target_dte_bucket"))
    raw_reason = recommendation.get("reason") or recommendation.get("summary") or ""
    if not raw_reason and isinstance(recommendation.get("thesis"), list):
        raw_reason = "; ".join(str(x).strip() for x in recommendation["thesis"][:3] if str(x).strip())
    reason = str(raw_reason or "").strip()

    if decision not in VALID_DECISIONS:
        return None
    if confidence not in VALID_CONFIDENCE_LEVELS:
        return None
    if not target_dte_bucket:
        target_dte_bucket = "none"
    if not reason:
        return None

    return {
        "decision": decision,
        "confidence": confidence,
        "target_dte_bucket": target_dte_bucket,
        "selected_option_id": None,
        "selected_expiration_date": None,
        "selected_strike_price": None,
        "selected_option_source": "manager_decision_only",
        "reason": reason,
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


def _extract_recommendation_from_text(raw_response: str) -> dict[str, Any] | None:
    text = str(raw_response or "").strip()
    if not text:
        return None

    normalized_text = text.replace("**", "")
    decision_match = re.search(
        r"recommendation\s*:?\s*(call|put|neither|bullish|bearish|neutral|hold|avoid|pass)",
        normalized_text,
        re.IGNORECASE,
    )
    confidence_match = re.search(
        r"confidence\s*:?\s*(high|medium|low|strong|moderate|weak)",
        normalized_text,
        re.IGNORECASE,
    )

    reason = _extract_labeled_section(
        normalized_text,
        "Reason",
        ["Recommendation", "Confidence", "Summary", "Thesis", "Risks"],
    )
    if not reason:
        reason = _extract_labeled_section(
            normalized_text,
            "Summary",
            ["Recommendation", "Confidence", "Reason", "Thesis", "Risks"],
        )
    if not reason:
        reason = normalized_text

    recommendation = {
        "decision": decision_match.group(1) if decision_match else "",
        "confidence": confidence_match.group(1) if confidence_match else "",
        "target_dte_bucket": "",
        "selected_option_id": "",
        "selected_expiration_date": "",
        "selected_strike_price": "",
        "reason": reason.strip(),
    }
    target_dte_bucket_match = re.search(
        r"(target[_\s]?dte[_\s]?bucket|dte[_\s]?bucket)\s*:?\s*(3_7|7_14|14_30|none|3-7|7-14|14-30)",
        normalized_text,
        re.IGNORECASE,
    )
    if target_dte_bucket_match:
        recommendation["target_dte_bucket"] = target_dte_bucket_match.group(2)
    selected_option_id_match = re.search(
        r"(selected[_\s]?option[_\s]?id|option[_\s]?id)\s*:?\s*(\d+)",
        normalized_text,
        re.IGNORECASE,
    )
    if selected_option_id_match:
        recommendation["selected_option_id"] = selected_option_id_match.group(2)

    selected_expiration_date_match = re.search(
        r"(selected[_\s]?expiration[_\s]?date|expiration[_\s]?date)\s*:?\s*(\d{4}-\d{2}-\d{2})",
        normalized_text,
        re.IGNORECASE,
    )
    if selected_expiration_date_match:
        recommendation["selected_expiration_date"] = selected_expiration_date_match.group(2)

    selected_strike_price_match = re.search(
        r"(selected[_\s]?strike[_\s]?price|strike[_\s]?price)\s*:?\s*(\d+(?:\.\d+)?)",
        normalized_text,
        re.IGNORECASE,
    )
    if selected_strike_price_match:
        recommendation["selected_strike_price"] = selected_strike_price_match.group(2)

    return recommendation


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
            "selected_option_id": None,
            "selected_expiration_date": None,
            "selected_strike_price": None,
            "selected_option_source": "manager_decision_only",
            "reason": "There was not enough processed article evidence or live market/account context available to support a call or put decision.",
            "selection_debug": {
                "manager_module_path": __file__,
                "manager_stage_version": MANAGER_STAGE_VERSION,
                "option_contract_count": len(market_context.get("option_market", {}).get("contracts", [])),
            },
        },
        "selected_option": None,
    }


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
    """Run the manager stage for one company and return a structured recommendation."""
    client = client or _get_default_client()
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
    payload["strategist_recommendation"] = _normalize_strategist_recommendation(
        strategist_recommendation
    )

    company = payload["company"]
    market_context = payload.get("market_context", {})
    option_contracts = market_context.get("option_market", {}).get("contracts", [])
    context_snapshot = _build_context_snapshot(payload)

    if not _payload_has_evidence(payload):
        return _build_no_evidence_result(
            company,
            context_snapshot=context_snapshot,
            market_context=market_context,
        )

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
    recommendation = _normalize_recommendation(
        _extract_recommendation(parsed),
        option_contracts=option_contracts,
        market_context=market_context,
    )
    if recommendation is None:
        recommendation = _normalize_recommendation(
            _extract_recommendation_from_text(raw_response),
            option_contracts=option_contracts,
            market_context=market_context,
        )
    if recommendation is None:
        raise RuntimeError(
            "Manager model returned an invalid response. "
            "Expected a JSON object with recommendation.decision/confidence/reason. "
            f"Raw response: {raw_response[:800]}"
        )

    recommendation = {
        **recommendation,
        "selection_debug": {
            "manager_module_path": __file__,
            "manager_stage_version": MANAGER_STAGE_VERSION,
            "option_contract_count": len(option_contracts),
        },
    }
    selected_option = None
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
        "recommendation": recommendation,
        "selected_option": selected_option,
    }


if __name__ == "__main__":
    print(json.dumps(decide_company_option_position("AAPL"), ensure_ascii=True, indent=2))
