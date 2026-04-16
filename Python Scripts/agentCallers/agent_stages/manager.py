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
MANAGER_STAGE_VERSION = "decision-only-v2"

VALID_DECISIONS = {"call", "put", "neither"}
VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}

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
                "reason": {"type": "string"},
            },
            "required": ["decision", "confidence", "reason"],
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


def _build_context_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    market_context = payload.get("market_context", {})
    strategist_recommendation = payload.get("strategist_recommendation", {})
    return {
        "view_counts": {
            key: int(value.get("count") or 0)
            for key, value in payload.get("views", {}).items()
            if isinstance(value, dict)
        },
        "article_summary_count": len(payload.get("supporting_articles", {}).get("article_summaries", [])),
        "full_article_count": len(payload.get("supporting_articles", {}).get("full_articles", [])),
        "stock_price_available": bool(market_context.get("current_stock_price", {}).get("available")),
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
    }


def _build_manager_visible_market_context(payload: dict[str, Any]) -> dict[str, Any]:
    market_context = payload.get("market_context", {})
    option_market = market_context.get("option_market", {})

    return {
        "current_stock_price": market_context.get("current_stock_price", {}),
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
        "Treat upstream agent conclusions as signals, not certainty, and weigh them against the article evidence, "
        "the current stock price snapshot, the supplied 1d, 5d, 1mo, and 3mo stock price history, "
        "account buying power, the current position state, and the supplied strategist recommendation. "
        "The strategist recommendation includes whether the company is a trade candidate, the expected stock direction, "
        "and the preferred options direction. Use that as one input, but do not follow it blindly if the stronger "
        "market or account context points elsewhere. "
        "Do not use or infer a specific option contract from Alpaca contract data. "
        "Contract selection happens in a separate deterministic step after your decision. "
        "Choose 'call' only when the combined evidence is convincingly bullish and the account context can support it. "
        "Choose 'put' only when the combined evidence is convincingly bearish and the account context can support it. "
        "If the evidence is mixed, weak, operationally constrained, or mostly inconclusive, prefer 'neither'. "
        "Do not choose a specific option contract yourself. "
        "Return only valid JSON with a top-level key named 'recommendation'. "
        "Do not include markdown fences, notes, or extra keys. "
        "The recommendation object must contain: decision, confidence, reason. "
        "decision must be one of: call, put, neither. "
        "confidence must be one of: high, medium, low. "
        "reason must be a short paragraph."
    )
    system_prompt = str(system_prompt_override or default_system_prompt)

    user_payload = {
        "task": str(task_override or "Decide whether the supplied company currently supports a call, put, or neither."),
        "company": payload["company"],
        "peer_groups": payload.get("peer_groups", {}),
        "filters": payload.get("filters", {}),
        "views": payload.get("views", {}),
        "supporting_articles": payload.get("supporting_articles", {}),
        "strategist_recommendation": payload.get("strategist_recommendation", {}),
        "market_context": _build_manager_visible_market_context(payload),
        "required_output": {
            "recommendation": {
                "decision": "call|put|neither",
                "confidence": "high|medium|low",
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

    if any(key in payload for key in ("decision", "confidence", "reason")):
        return payload

    for key in ("output_schema", "required_output", "result", "analysis"):
        nested = payload.get(key)
        if not isinstance(nested, dict):
            continue

        nested_recommendation = nested.get("recommendation")
        if isinstance(nested_recommendation, dict):
            return nested_recommendation

        if any(inner_key in nested for inner_key in ("decision", "confidence", "reason")):
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
    for key in ("midpoint_price", "price", "ask_price", "bid_price"):
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
    reason = str(
        recommendation.get("reason")
        or recommendation.get("summary")
        or recommendation.get("thesis")
        or ""
    ).strip()

    if decision not in VALID_DECISIONS:
        return None
    if confidence not in VALID_CONFIDENCE_LEVELS:
        return None
    if not reason:
        return None

    return {
        "decision": decision,
        "confidence": confidence,
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
        "selected_option_id": "",
        "selected_expiration_date": "",
        "selected_strike_price": "",
        "reason": reason.strip(),
    }
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
    payload["strategist_recommendation"] = dict(strategist_recommendation or {})

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
