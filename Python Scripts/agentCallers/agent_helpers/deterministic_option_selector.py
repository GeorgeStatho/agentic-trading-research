from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
from typing import Any


SELECTOR_VERSION = "deterministic-selector-v3-one-dollar-otm-with-fallback"
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


def _is_one_dollar_otm_contract(
    contract_type: str,
    strike_price: float | None,
    reference_stock_price: float | None,
) -> bool:
    if strike_price is None or reference_stock_price is None:
        return False

    if contract_type == "call":
        return strike_price >= (reference_stock_price + 1.0)

    if contract_type == "put":
        return strike_price <= (reference_stock_price - 1.0)

    return False


def _is_valid_fallback_side_contract(
    contract_type: str,
    strike_price: float | None,
    reference_stock_price: float | None,
) -> bool:
    if strike_price is None or reference_stock_price is None:
        return False

    if contract_type == "call":
        return strike_price >= reference_stock_price

    if contract_type == "put":
        return strike_price <= reference_stock_price

    return False


def _target_distance(
    contract_type: str,
    strike_price: float | None,
    reference_stock_price: float | None,
    *,
    one_dollar_otm: bool,
) -> float:
    if strike_price is None or reference_stock_price is None:
        return float("inf")

    if contract_type == "call":
        target = reference_stock_price + 1.0 if one_dollar_otm else reference_stock_price
        return abs(strike_price - target)

    if contract_type == "put":
        target = reference_stock_price - 1.0 if one_dollar_otm else reference_stock_price
        return abs(strike_price - target)

    return float("inf")


def _sort_key(
    contract: dict[str, Any],
    *,
    normalized_decision: str,
    reference_stock_price: float | None,
    one_dollar_otm: bool,
) -> tuple[float, float, str, float, int]:
    contract_price = _get_contract_market_price(contract)
    has_contract_price = 0.0 if contract_price is not None else 1.0

    strike_price = _coerce_float(contract.get("strike_price"))
    distance = _target_distance(
        normalized_decision,
        strike_price,
        reference_stock_price,
        one_dollar_otm=one_dollar_otm,
    )

    expiration_date = str(contract.get("expiration_date") or "9999-12-31")
    open_interest = _coerce_float(contract.get("open_interest"))
    open_interest_rank = -(open_interest if open_interest is not None else -1.0)
    option_id = _normalize_option_id(contract.get("option_id")) or 10**9

    return (
        has_contract_price,
        distance,
        expiration_date,
        open_interest_rank,
        option_id,
    )


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
        LOGGER.info("No contracts matched decision=%s.", normalized_decision)
        return None

    preferred_contracts = [
        contract
        for contract in matching_contracts
        if _is_one_dollar_otm_contract(
            normalized_decision,
            _coerce_float(contract.get("strike_price")),
            reference_stock_price,
        )
    ]

    fallback_contracts = [
        contract
        for contract in matching_contracts
        if _is_valid_fallback_side_contract(
            normalized_decision,
            _coerce_float(contract.get("strike_price")),
            reference_stock_price,
        )
    ]

    LOGGER.info(
        "Reference stock price for decision=%s was %s. Found %s preferred $1+ OTM contracts and %s fallback side-correct contracts.",
        normalized_decision,
        reference_stock_price,
        len(preferred_contracts),
        len(fallback_contracts),
    )

    if preferred_contracts:
        candidate_pool = preferred_contracts
        selection_mode = "preferred_one_dollar_otm"
        candidate_pool.sort(
            key=lambda contract: _sort_key(
                contract,
                normalized_decision=normalized_decision,
                reference_stock_price=reference_stock_price,
                one_dollar_otm=True,
            )
        )
    elif fallback_contracts:
        candidate_pool = fallback_contracts
        selection_mode = "fallback_closest_side_correct"
        candidate_pool.sort(
            key=lambda contract: _sort_key(
                contract,
                normalized_decision=normalized_decision,
                reference_stock_price=reference_stock_price,
                one_dollar_otm=False,
            )
        )
    else:
        LOGGER.info(
            "No usable %s contracts were available for either preferred or fallback selection.",
            normalized_decision,
        )
        return None

    selected = dict(candidate_pool[0])
    selected["_selection_mode"] = selection_mode

    LOGGER.info(
        "Selected option for decision=%s mode=%s option_id=%s symbol=%s strike=%s expiration=%s reference_stock_price=%s",
        normalized_decision,
        selection_mode,
        selected.get("option_id"),
        selected.get("symbol"),
        selected.get("strike_price"),
        selected.get("expiration_date"),
        reference_stock_price,
    )

    return selected


def apply_deterministic_option_selection(manager_result: dict[str, Any]) -> dict[str, Any]:
    recommendation = dict(manager_result.get("recommendation") or {})
    market_context = dict(manager_result.get("market_context") or {})
    option_market = dict(market_context.get("option_market") or {})
    option_contracts = list(option_market.get("contracts") or [])

    decision = _normalize_decision(recommendation.get("decision"))
    confidence = _normalize_confidence(recommendation.get("confidence"))
    selection_filters = dict(option_market.get("selection_filters") or {})
    reference_stock_price = _get_reference_stock_price(market_context)

    selected_option: dict[str, Any] | None = None
    selected_option_source = "not_applicable"
    selection_mode = ""

    if decision in {"call", "put"} and confidence == "high":
        selected_option = _pick_matching_contract(
            decision=decision,
            option_contracts=option_contracts,
            market_context=market_context,
        )
        selection_mode = str(selected_option.get("_selection_mode") or "") if selected_option else ""
        if selected_option is not None:
            selected_option = {k: v for k, v in selected_option.items() if k != "_selection_mode"}

        selected_option_source = (
            f"deterministic_high_confidence_{selection_mode}"
            if selected_option is not None and selection_mode
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
            "reference_stock_price": reference_stock_price,
            "option_contract_count": len(option_contracts),
            "matching_contract_count": sum(
                1
                for contract in option_contracts
                if _normalize_contract_type(contract.get("contract_type")) == decision
            ),
            "strict_one_dollar_otm_matching_count": sum(
                1
                for contract in option_contracts
                if _normalize_contract_type(contract.get("contract_type")) == decision
                and _is_one_dollar_otm_contract(
                    decision,
                    _coerce_float(contract.get("strike_price")),
                    reference_stock_price,
                )
            ),
            "fallback_side_correct_matching_count": sum(
                1
                for contract in option_contracts
                if _normalize_contract_type(contract.get("contract_type")) == decision
                and _is_valid_fallback_side_contract(
                    decision,
                    _coerce_float(contract.get("strike_price")),
                    reference_stock_price,
                )
            ),
            "selection_mode": selection_mode,
            "selected_option_id": selected_option_id,
            "selected_option_symbol": (
                str(selected_option.get("symbol") or "").strip()
                if selected_option
                else ""
            ),
            "selected_option_strike_price": selected_strike_price,
            "selected_option_expiration_date": selected_expiration_date,
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
    import copy
    from agent_helpers.manager import test_market_context

    symbol = str(sys.argv[1] if len(sys.argv) > 1 else "AAPL").strip().upper()
    market_test = test_market_context(symbol)
    market_context = market_test.get("market_context", {})

    base_manager_result = {
        "company": market_test.get("company", {}),
        "context_snapshot": market_test.get("diagnostics", {}),
        "market_context": market_context,
        "selected_option": None,
    }

    for decision in ("call", "put"):
        test_manager_result = copy.deepcopy(base_manager_result)
        test_manager_result["recommendation"] = {
            "decision": decision,
            "confidence": "high",
            "reason": f"Manual test for {decision}",
        }

        selected_result = apply_deterministic_option_selection(test_manager_result)

        print(f"\n=== TEST DECISION: {decision.upper()} ===")
        print(json.dumps(selected_result, ensure_ascii=True, indent=2))