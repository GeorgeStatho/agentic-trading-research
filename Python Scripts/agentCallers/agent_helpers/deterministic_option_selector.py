from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
from typing import Any


SELECTOR_VERSION = "deterministic-selector-v1"
LOGGER = logging.getLogger(__name__)

AGENT_HELPERS_DIR = Path(__file__).resolve().parent
AGENT_CALLERS_DIR = AGENT_HELPERS_DIR.parent
PYTHON_SCRIPTS_DIR = AGENT_CALLERS_DIR.parent
DATA_DIR = PYTHON_SCRIPTS_DIR.parent / "Data"

for path in (AGENT_CALLERS_DIR, PYTHON_SCRIPTS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.append(normalized)


def _coerce_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_option_id(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
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
        "neutral": "neither",
    }
    decision = replacements.get(decision, decision)
    return decision if decision in {"call", "put", "neither"} else ""


def _normalize_confidence(value: Any) -> str:
    confidence = str(value or "").strip().lower()
    if confidence in {"high", "medium", "low"}:
        return confidence
    return ""


def _normalize_contract_type(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    if enum_value not in (None, ""):
        contract_type = str(enum_value).strip().lower()
    else:
        contract_type = str(value or "").strip().lower()

    if "." in contract_type:
        contract_type = contract_type.rsplit(".", 1)[-1]

    replacements = {
        "c": "call",
        "call_option": "call",
        "call option": "call",
        "calls": "call",
        "calls_option": "call",
        "p": "put",
        "put_option": "put",
        "put option": "put",
        "puts": "put",
        "puts_option": "put",
    }
    contract_type = replacements.get(contract_type, contract_type)
    return contract_type if contract_type in {"call", "put"} else ""


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


def _is_otm_contract(contract_type: str, strike_price: float | None, reference_stock_price: float | None) -> bool:
    if strike_price is None or reference_stock_price is None:
        return False
    if contract_type == "call":
        return strike_price >= reference_stock_price
    if contract_type == "put":
        return strike_price <= reference_stock_price
    return False


def _pick_matching_contract(
    *,
    decision: str,
    option_contracts: list[dict[str, Any]],
    market_context: dict[str, Any],
) -> dict[str, Any] | None:
    normalized_decision = _normalize_decision(decision)
    if normalized_decision not in {"call", "put"}:
        return None

    reference_stock_price = _get_reference_stock_price(market_context)
    seen_contract_types = [
        str(contract.get("contract_type") or "").strip()
        for contract in option_contracts[:6]
    ]
    matching_contracts = [
        contract
        for contract in option_contracts
        if _normalize_contract_type(contract.get("contract_type")) == normalized_decision
    ]
    LOGGER.info(
        "Deterministic selector saw contract types=%s for decision=%s and matched %s of %s contracts",
        seen_contract_types,
        normalized_decision,
        len(matching_contracts),
        len(option_contracts),
    )
    if not matching_contracts:
        return None

    def sort_key(contract: dict[str, Any]) -> tuple[float, float, float, str, float, int]:
        contract_price = _get_contract_market_price(contract)
        has_contract_price = 0.0 if contract_price is not None else 1.0

        strike_price = _coerce_float(contract.get("strike_price"))
        strike_distance = (
            abs(strike_price - reference_stock_price)
            if strike_price is not None and reference_stock_price is not None
            else float("inf")
        )
        otm_preference = 0.0 if _is_otm_contract(normalized_decision, strike_price, reference_stock_price) else 1.0

        expiration_date = str(contract.get("expiration_date") or "9999-12-31")
        open_interest = _coerce_float(contract.get("open_interest"))
        open_interest_rank = -(open_interest if open_interest is not None else -1.0)
        option_id = _normalize_option_id(contract.get("option_id")) or 10**9

        return (
            has_contract_price,
            otm_preference,
            strike_distance,
            expiration_date,
            open_interest_rank,
            option_id,
        )

    return min(matching_contracts, key=sort_key)


def apply_deterministic_option_selection(manager_result: dict[str, Any]) -> dict[str, Any]:
    recommendation = dict(manager_result.get("recommendation") or {})
    market_context = dict(manager_result.get("market_context") or {})
    option_market = dict(market_context.get("option_market") or {})
    option_contracts = list(option_market.get("contracts") or [])

    decision = _normalize_decision(recommendation.get("decision"))
    confidence = _normalize_confidence(recommendation.get("confidence"))
    selection_filters = dict(option_market.get("selection_filters") or {})

    selected_option: dict[str, Any] | None = None
    selected_option_source = "not_applicable"

    if decision in {"call", "put"} and confidence == "high":
        selected_option = _pick_matching_contract(
            decision=decision,
            option_contracts=option_contracts,
            market_context=market_context,
        )
        selected_option_source = (
            "deterministic_high_confidence"
            if selected_option is not None
            else "unavailable"
        )
    elif decision in {"call", "put"}:
        selected_option_source = "confidence_below_high"

    selected_option_id = _normalize_option_id(selected_option.get("option_id")) if selected_option else None
    selected_expiration_date = (
        str(selected_option.get("expiration_date") or "").strip() or None
        if selected_option
        else None
    )
    selected_strike_price = (
        _coerce_float(selected_option.get("strike_price"))
        if selected_option
        else None
    )

    updated_recommendation = {
        **recommendation,
        "selected_option_id": selected_option_id,
        "selected_expiration_date": selected_expiration_date,
        "selected_strike_price": selected_strike_price,
        "selected_option_source": selected_option_source,
        "selection_debug": {
            "selector_version": SELECTOR_VERSION,
            "decision_seen": decision,
            "confidence_seen": confidence,
            "option_contract_count": len(option_contracts),
            "matching_contract_count": sum(
                1
                for contract in option_contracts
                if _normalize_contract_type(contract.get("contract_type")) == decision
            ),
            "selected_option_id": selected_option_id,
            "seen_contract_types": [
                str(contract.get("contract_type") or "").strip()
                for contract in option_contracts[:6]
            ],
            "selection_filters": selection_filters,
        },
    }

    return {
        **manager_result,
        "recommendation": updated_recommendation,
        "selected_option": selected_option,
    }


if __name__ == "__main__":
    from agent_stages.manager import decide_company_option_position

    symbol = str(sys.argv[1] if len(sys.argv) > 1 else "AAPL").strip().upper()
    base_result = decide_company_option_position(symbol)
    selected_result = apply_deterministic_option_selection(base_result)
    print(json.dumps(selected_result, ensure_ascii=True, indent=2))
