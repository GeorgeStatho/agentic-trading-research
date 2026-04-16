from __future__ import annotations

"""Deterministically choose an option contract after the manager stage decides direction.

This module exists so the LLM only decides directional intent. Contract
selection remains fully rule-based, which makes the final output easier to
debug and safer to tune.
"""

import copy
import json
import logging
from datetime import date, datetime
from pathlib import Path
import sys
from typing import Any


SELECTOR_VERSION = "deterministic-selector-v4-short-swing-greeks"

LOGGER = logging.getLogger(__name__)

if __package__ in {None, ""}:
    AGENT_CALLERS_DIR = Path(__file__).resolve().parents[1]
    if str(AGENT_CALLERS_DIR) not in sys.path:
        sys.path.append(str(AGENT_CALLERS_DIR))

from _paths import bootstrap_agent_callers


bootstrap_agent_callers()


# =========================
# TUNABLE DEFAULTS
# =========================
MIN_DTE = 3
MAX_DTE = 10

MIN_OPEN_INTEREST = 500.0
MAX_SPREAD_PCT = 0.15

MIN_ABS_DELTA = 0.25
MAX_ABS_DELTA = 0.45
TARGET_ABS_DELTA = 0.35

MIN_GAMMA = 0.03
MAX_THETA_TO_PRICE = 0.35

REQUIRE_ONE_DOLLAR_OTM = False
PREFERRED_OTM_DISTANCE = 1.0


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


def _get_latest_quote(contract: dict[str, Any]) -> dict[str, Any]:
    latest_quote = contract.get("latest_quote", {})
    return latest_quote if isinstance(latest_quote, dict) else {}


def _get_bid_price(contract: dict[str, Any]) -> float | None:
    return _coerce_float(_get_latest_quote(contract).get("bid_price"))


def _get_ask_price(contract: dict[str, Any]) -> float | None:
    return _coerce_float(_get_latest_quote(contract).get("ask_price"))


def _get_contract_market_price(contract: dict[str, Any]) -> float | None:
    latest_quote = _get_latest_quote(contract)

    midpoint = _coerce_float(latest_quote.get("midpoint_price"))
    if midpoint is not None and midpoint > 0:
        return midpoint

    bid = _coerce_float(latest_quote.get("bid_price"))
    ask = _coerce_float(latest_quote.get("ask_price"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0

    for key in ("latest_trade_price", "close_price"):
        price = _coerce_float(contract.get(key))
        if price is not None and price > 0:
            return price

    return None


def _get_greek(contract: dict[str, Any], name: str) -> float | None:
    greeks = contract.get("greeks", {})
    if not isinstance(greeks, dict):
        return None
    return _coerce_float(greeks.get(name))


def _get_dte(contract: dict[str, Any]) -> int | None:
    expiration_text = str(contract.get("expiration_date") or "").strip()
    if not expiration_text:
        return None

    try:
        expiration = datetime.strptime(expiration_text, "%Y-%m-%d").date()
    except ValueError:
        return None

    return (expiration - date.today()).days


def _get_spread_pct(contract: dict[str, Any]) -> float | None:
    bid = _get_bid_price(contract)
    ask = _get_ask_price(contract)
    mid = _get_contract_market_price(contract)

    if bid is None or ask is None or mid is None or mid <= 0:
        return None

    spread = ask - bid
    if spread < 0:
        return None

    return spread / mid


def _get_theta_to_price(contract: dict[str, Any]) -> float | None:
    theta = _get_greek(contract, "theta")
    price = _get_contract_market_price(contract)
    if theta is None or price is None or price <= 0:
        return None
    return abs(theta) / price


def _is_otm_contract(
    contract_type: str,
    strike_price: float | None,
    reference_stock_price: float | None,
) -> bool:
    if strike_price is None or reference_stock_price is None:
        return False

    if contract_type == "call":
        return strike_price > reference_stock_price

    if contract_type == "put":
        return strike_price < reference_stock_price

    return False


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


def _otm_distance(
    contract_type: str,
    strike_price: float | None,
    reference_stock_price: float | None,
) -> float:
    if strike_price is None or reference_stock_price is None:
        return float("inf")

    if contract_type == "call":
        return max(0.0, strike_price - reference_stock_price)

    if contract_type == "put":
        return max(0.0, reference_stock_price - strike_price)

    return float("inf")


def _distance_to_preferred_otm(
    contract_type: str,
    strike_price: float | None,
    reference_stock_price: float | None,
) -> float:
    return abs(
        _otm_distance(contract_type, strike_price, reference_stock_price)
        - PREFERRED_OTM_DISTANCE
    )


def _basic_sort_key(
    contract: dict[str, Any],
    *,
    normalized_decision: str,
    reference_stock_price: float | None,
    prefer_one_dollar_otm: bool,
) -> tuple[float, float, str, float, int]:
    contract_price = _get_contract_market_price(contract)
    has_contract_price = 0.0 if contract_price is not None else 1.0

    strike_price = _coerce_float(contract.get("strike_price"))
    if prefer_one_dollar_otm:
        distance = _distance_to_preferred_otm(
            normalized_decision,
            strike_price,
            reference_stock_price,
        )
    else:
        distance = _otm_distance(
            normalized_decision,
            strike_price,
            reference_stock_price,
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


def _contract_debug_snapshot(
    contract: dict[str, Any],
    *,
    reference_stock_price: float | None,
) -> dict[str, Any]:
    strike_price = _coerce_float(contract.get("strike_price"))
    return {
        "option_id": _normalize_option_id(contract.get("option_id")),
        "symbol": str(contract.get("symbol") or "").strip(),
        "contract_type": _normalize_contract_type(contract.get("contract_type")),
        "expiration_date": str(contract.get("expiration_date") or "").strip(),
        "strike_price": strike_price,
        "reference_stock_price": reference_stock_price,
        "otm_distance": _otm_distance(
            _normalize_contract_type(contract.get("contract_type")),
            strike_price,
            reference_stock_price,
        ),
        "market_price": _get_contract_market_price(contract),
        "bid_price": _get_bid_price(contract),
        "ask_price": _get_ask_price(contract),
        "spread_pct": _get_spread_pct(contract),
        "open_interest": _coerce_float(contract.get("open_interest")),
        "dte": _get_dte(contract),
        "delta": _get_greek(contract, "delta"),
        "gamma": _get_greek(contract, "gamma"),
        "theta": _get_greek(contract, "theta"),
        "vega": _get_greek(contract, "vega"),
        "theta_to_price": _get_theta_to_price(contract),
    }


def _passes_short_swing_filters(
    contract: dict[str, Any],
    *,
    normalized_decision: str,
    reference_stock_price: float | None,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    strike_price = _coerce_float(contract.get("strike_price"))
    if strike_price is None:
        reasons.append("missing_strike_price")
        return False, reasons

    if REQUIRE_ONE_DOLLAR_OTM:
        if not _is_one_dollar_otm_contract(
            normalized_decision, strike_price, reference_stock_price
        ):
            reasons.append("not_one_dollar_otm")
    else:
        if not _is_otm_contract(normalized_decision, strike_price, reference_stock_price):
            reasons.append("not_otm")

    dte = _get_dte(contract)
    if dte is None:
        reasons.append("missing_dte")
    elif dte < MIN_DTE:
        reasons.append("dte_below_min")
    elif dte > MAX_DTE:
        reasons.append("dte_above_max")

    delta = _get_greek(contract, "delta")
    abs_delta = abs(delta) if delta is not None else None
    if abs_delta is None:
        reasons.append("missing_delta")
    else:
        if abs_delta < MIN_ABS_DELTA:
            reasons.append("delta_below_min")
        if abs_delta > MAX_ABS_DELTA:
            reasons.append("delta_above_max")

    gamma = _get_greek(contract, "gamma")
    if gamma is None:
        reasons.append("missing_gamma")
    elif gamma < MIN_GAMMA:
        reasons.append("gamma_below_min")

    open_interest = _coerce_float(contract.get("open_interest"))
    if open_interest is None:
        reasons.append("missing_open_interest")
    elif open_interest < MIN_OPEN_INTEREST:
        reasons.append("open_interest_below_min")

    spread_pct = _get_spread_pct(contract)
    if spread_pct is None:
        reasons.append("missing_spread_pct")
    elif spread_pct > MAX_SPREAD_PCT:
        reasons.append("spread_above_max")

    theta_to_price = _get_theta_to_price(contract)
    if theta_to_price is None:
        reasons.append("missing_theta_to_price")
    elif theta_to_price > MAX_THETA_TO_PRICE:
        reasons.append("theta_to_price_above_max")

    return len(reasons) == 0, reasons


def _short_swing_score(
    contract: dict[str, Any],
    *,
    normalized_decision: str,
    reference_stock_price: float | None,
) -> tuple[float, float, float, float, float, float, str, int]:
    abs_delta = abs(_get_greek(contract, "delta") or 0.0)
    spread_pct = _get_spread_pct(contract) or 999.0
    theta_to_price = _get_theta_to_price(contract) or 999.0
    gamma = _get_greek(contract, "gamma") or 0.0
    open_interest = _coerce_float(contract.get("open_interest")) or 0.0
    strike_price = _coerce_float(contract.get("strike_price"))
    otm_distance_pref = _distance_to_preferred_otm(
        normalized_decision,
        strike_price,
        reference_stock_price,
    )
    expiration_date = str(contract.get("expiration_date") or "9999-12-31")
    option_id = _normalize_option_id(contract.get("option_id")) or 10**9

    return (
        abs(abs_delta - TARGET_ABS_DELTA),
        spread_pct,
        theta_to_price,
        -gamma,
        -open_interest,
        otm_distance_pref,
        expiration_date,
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
        "Selector saw contract types=%s for decision=%s and matched %s of %s contracts",
        seen_contract_types,
        normalized_decision,
        len(matching_contracts),
        len(option_contracts),
    )

    if not matching_contracts:
        LOGGER.info("No contracts matched decision=%s.", normalized_decision)
        return None

    passed_contracts: list[dict[str, Any]] = []
    rejected_contracts_debug: list[dict[str, Any]] = []

    for contract in matching_contracts:
        passed, reasons = _passes_short_swing_filters(
            contract,
            normalized_decision=normalized_decision,
            reference_stock_price=reference_stock_price,
        )
        if passed:
            passed_contracts.append(contract)
        else:
            rejected_contracts_debug.append(
                {
                    **_contract_debug_snapshot(
                        contract,
                        reference_stock_price=reference_stock_price,
                    ),
                    "rejection_reasons": reasons,
                }
            )

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
        "Reference stock price=%s decision=%s passed_short_swing=%s preferred_one_dollar_otm=%s fallback_side_correct=%s",
        reference_stock_price,
        normalized_decision,
        len(passed_contracts),
        len(preferred_contracts),
        len(fallback_contracts),
    )

    if passed_contracts:
        candidate_pool = list(passed_contracts)
        selection_mode = "greeks_filtered_short_swing"
        candidate_pool.sort(
            key=lambda contract: _short_swing_score(
                contract,
                normalized_decision=normalized_decision,
                reference_stock_price=reference_stock_price,
            )
        )
    elif preferred_contracts:
        candidate_pool = list(preferred_contracts)
        selection_mode = "fallback_preferred_one_dollar_otm"
        candidate_pool.sort(
            key=lambda contract: _basic_sort_key(
                contract,
                normalized_decision=normalized_decision,
                reference_stock_price=reference_stock_price,
                prefer_one_dollar_otm=True,
            )
        )
    elif fallback_contracts:
        candidate_pool = list(fallback_contracts)
        selection_mode = "fallback_closest_side_correct"
        candidate_pool.sort(
            key=lambda contract: _basic_sort_key(
                contract,
                normalized_decision=normalized_decision,
                reference_stock_price=reference_stock_price,
                prefer_one_dollar_otm=False,
            )
        )
    else:
        LOGGER.info(
            "No usable %s contracts were available for short-swing, preferred, or fallback selection.",
            normalized_decision,
        )
        return None

    selected = dict(candidate_pool[0])
    selected["_selection_mode"] = selection_mode
    selected["_selector_debug"] = {
        "reference_stock_price": reference_stock_price,
        "passed_short_swing_count": len(passed_contracts),
        "rejected_short_swing_count": len(rejected_contracts_debug),
        "rejected_short_swing_examples": rejected_contracts_debug[:10],
        "candidate_pool_count": len(candidate_pool),
        "selection_mode": selection_mode,
        "selected_contract_snapshot": _contract_debug_snapshot(
            selected,
            reference_stock_price=reference_stock_price,
        ),
    }

    LOGGER.info(
        "Selected option decision=%s mode=%s option_id=%s symbol=%s strike=%s expiration=%s reference_stock_price=%s",
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
    """Attach a concrete option contract to a manager result when rules allow it.

    Usage:
        Pass the dict returned by ``agent_stages.manager.decide_company_option_position``.
        The helper preserves the original payload and adds ``selected_option``
        plus selection diagnostics under ``recommendation.selection_debug``.
    """
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
    selector_debug: dict[str, Any] = {}

    if decision in {"call", "put"} and confidence == "high":
        selected_option = _pick_matching_contract(
            decision=decision,
            option_contracts=option_contracts,
            market_context=market_context,
        )

        if selected_option is not None:
            selection_mode = str(selected_option.get("_selection_mode") or "")
            selector_debug = dict(selected_option.get("_selector_debug") or {})
            selected_option = {
                k: v
                for k, v in selected_option.items()
                if k not in {"_selection_mode", "_selector_debug"}
            }

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
            "short_swing_thresholds": {
                "min_dte": MIN_DTE,
                "max_dte": MAX_DTE,
                "min_open_interest": MIN_OPEN_INTEREST,
                "max_spread_pct": MAX_SPREAD_PCT,
                "min_abs_delta": MIN_ABS_DELTA,
                "max_abs_delta": MAX_ABS_DELTA,
                "target_abs_delta": TARGET_ABS_DELTA,
                "min_gamma": MIN_GAMMA,
                "max_theta_to_price": MAX_THETA_TO_PRICE,
                "require_one_dollar_otm": REQUIRE_ONE_DOLLAR_OTM,
                "preferred_otm_distance": PREFERRED_OTM_DISTANCE,
            },
            "selection_mode": selection_mode,
            "selected_option_id": selected_option_id,
            "selected_option_symbol": (
                str(selected_option.get("symbol") or "").strip()
                if selected_option
                else ""
            ),
            "selected_option_strike_price": selected_strike_price,
            "selected_option_expiration_date": selected_expiration_date,
            "selected_option_snapshot": (
                _contract_debug_snapshot(
                    selected_option,
                    reference_stock_price=reference_stock_price,
                )
                if selected_option
                else {}
            ),
            "seen_contract_types": [
                str(contract.get("contract_type") or "").strip()
                for contract in option_contracts[:6]
            ],
            "selection_filters": selection_filters,
            "selector_debug": selector_debug,
        },
    }

    return {
        **manager_result,
        "recommendation": updated_recommendation,
        "selected_option": selected_option,
    }


if __name__ == "__main__":
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
