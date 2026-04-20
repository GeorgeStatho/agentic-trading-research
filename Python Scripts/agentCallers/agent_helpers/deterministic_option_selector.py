from __future__ import annotations

"""Deterministically choose an option contract after the manager stage decides direction.

This module exists so the LLM only decides directional intent. Contract
selection remains fully rule-based, which makes the final output easier to
debug and safer to tune.
"""

import copy
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
import sys
from typing import Any


SELECTOR_VERSION = "deterministic-selector-v5-simple-hybrid-greeks"

LOGGER = logging.getLogger(__name__)

if __package__ in {None, ""}:
    AGENT_CALLERS_DIR = Path(__file__).resolve().parents[1]
    if str(AGENT_CALLERS_DIR) not in sys.path:
        sys.path.append(str(AGENT_CALLERS_DIR))

from _paths import bootstrap_agent_callers


bootstrap_agent_callers()


# =========================
# ENV HELPERS
# =========================
def _env_flag(name: str, default: bool) -> bool:
    value = str(os.getenv(name, str(default))).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _env_str(name: str, default: str) -> str:
    value = str(os.getenv(name, default)).strip().lower()
    return value or default.lower()


OPTION_SELECTOR_MODE = _env_str("OPTION_SELECTOR_MODE", "hybrid")
ALLOW_RISKY_SIMPLE_FALLBACK = _env_flag("ALLOW_RISKY_SIMPLE_FALLBACK", True)


# =========================
# SIMPLE MODE TUNABLES
# =========================
SIMPLE_REQUIRE_ONE_DOLLAR_OTM = True
SIMPLE_PREFERRED_OTM_DISTANCE = 1.0


# =========================
# HYBRID MODE TUNABLES
# keeps old short-DTE / near-strike behavior
# but filters obvious junk
# =========================
HYBRID_MIN_DTE = 1
HYBRID_MAX_DTE = 3
HYBRID_MIN_OPEN_INTEREST = 300.0
HYBRID_MAX_SPREAD_PCT = 0.18
HYBRID_MIN_ABS_DELTA = 0.15
HYBRID_TARGET_ABS_DELTA = 0.28
HYBRID_MAX_THETA_TO_PRICE = 0.80
HYBRID_MIN_GAMMA = 0.01
HYBRID_PREFERRED_OTM_DISTANCE = 1.0
HYBRID_REQUIRE_MIN_OTM_DISTANCE = True


# =========================
# GREEKS MODE TUNABLES
# stricter / more swing-like
# =========================
MIN_DTE = 1
MAX_DTE = 5
MIN_OPEN_INTEREST = 300.0
MAX_SPREAD_PCT = 0.18
MIN_ABS_DELTA = 0.15
MAX_ABS_DELTA = 0.28
TARGET_ABS_DELTA = 0.28
MIN_GAMMA = 0.01
MAX_THETA_TO_PRICE = 0.80
REQUIRE_ONE_DOLLAR_OTM = True
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


def _meets_min_otm_distance(
    contract_type: str,
    strike_price: float | None,
    reference_stock_price: float | None,
    *,
    min_distance: float,
) -> bool:
    if strike_price is None or reference_stock_price is None:
        return False
    return _otm_distance(contract_type, strike_price, reference_stock_price) >= min_distance


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


def _distance_to_target_otm(
    contract_type: str,
    strike_price: float | None,
    reference_stock_price: float | None,
    *,
    target_distance: float,
) -> float:
    return abs(
        _otm_distance(contract_type, strike_price, reference_stock_price)
        - target_distance
    )


def _basic_sort_key(
    contract: dict[str, Any],
    *,
    normalized_decision: str,
    reference_stock_price: float | None,
    prefer_target_otm: bool,
    target_distance: float,
) -> tuple[float, float, str, float, int]:
    contract_price = _get_contract_market_price(contract)
    has_contract_price = 0.0 if contract_price is not None else 1.0

    strike_price = _coerce_float(contract.get("strike_price"))
    if prefer_target_otm:
        distance = _distance_to_target_otm(
            normalized_decision,
            strike_price,
            reference_stock_price,
            target_distance=target_distance,
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


# =========================
# SIMPLE MODE
# =========================
def _pick_matching_contract_simple(
    *,
    decision: str,
    option_contracts: list[dict[str, Any]],
    market_context: dict[str, Any],
) -> dict[str, Any] | None:
    normalized_decision = _normalize_decision(decision)
    if normalized_decision not in {"call", "put"}:
        return None

    reference_stock_price = _get_reference_stock_price(market_context)

    matching_contracts = [
        contract
        for contract in option_contracts
        if _normalize_contract_type(contract.get("contract_type")) == normalized_decision
    ]
    if not matching_contracts:
        return None

    preferred_contracts = [
        contract
        for contract in matching_contracts
        if (
            _is_one_dollar_otm_contract(
                normalized_decision,
                _coerce_float(contract.get("strike_price")),
                reference_stock_price,
            )
            if SIMPLE_REQUIRE_ONE_DOLLAR_OTM
            else _is_otm_contract(
                normalized_decision,
                _coerce_float(contract.get("strike_price")),
                reference_stock_price,
            )
        )
    ]

    fallback_contracts = [
        contract
        for contract in matching_contracts
        if _is_otm_contract(
            normalized_decision,
            _coerce_float(contract.get("strike_price")),
            reference_stock_price,
        )
    ]

    if preferred_contracts:
        candidate_pool = list(preferred_contracts)
        selection_mode = "simple_preferred_near_otm"
        candidate_pool.sort(
            key=lambda contract: _basic_sort_key(
                contract,
                normalized_decision=normalized_decision,
                reference_stock_price=reference_stock_price,
                prefer_target_otm=True,
                target_distance=SIMPLE_PREFERRED_OTM_DISTANCE,
            )
        )
    elif fallback_contracts:
        candidate_pool = list(fallback_contracts)
        selection_mode = "simple_fallback_closest_side_correct"
        candidate_pool.sort(
            key=lambda contract: _basic_sort_key(
                contract,
                normalized_decision=normalized_decision,
                reference_stock_price=reference_stock_price,
                prefer_target_otm=False,
                target_distance=SIMPLE_PREFERRED_OTM_DISTANCE,
            )
        )
    else:
        return None

    selected = dict(candidate_pool[0])
    selected["_selection_mode"] = selection_mode
    selected["_selector_debug"] = {
        "selector_mode_requested": OPTION_SELECTOR_MODE,
        "reference_stock_price": reference_stock_price,
        "matching_contract_count": len(matching_contracts),
        "preferred_contract_count": len(preferred_contracts),
        "fallback_contract_count": len(fallback_contracts),
        "candidate_pool_count": len(candidate_pool),
        "selection_mode": selection_mode,
        "selected_contract_snapshot": _contract_debug_snapshot(
            selected,
            reference_stock_price=reference_stock_price,
        ),
    }
    return selected


# =========================
# HYBRID MODE
# =========================
def _passes_hybrid_fast_filters(
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

    if not _is_valid_fallback_side_contract(
        normalized_decision,
        strike_price,
        reference_stock_price,
    ):
        reasons.append("wrong_side")
        return False, reasons

    if HYBRID_REQUIRE_MIN_OTM_DISTANCE and not _meets_min_otm_distance(
        normalized_decision,
        strike_price,
        reference_stock_price,
        min_distance=HYBRID_PREFERRED_OTM_DISTANCE,
    ):
        reasons.append("otm_distance_below_preferred_min")
        return False, reasons

    dte = _get_dte(contract)
    if dte is None:
        reasons.append("missing_dte")
    elif dte < HYBRID_MIN_DTE:
        reasons.append("dte_below_min")
    elif dte > HYBRID_MAX_DTE:
        reasons.append("dte_above_max")

    open_interest = _coerce_float(contract.get("open_interest"))
    if open_interest is None:
        reasons.append("missing_open_interest")
    elif open_interest < HYBRID_MIN_OPEN_INTEREST:
        reasons.append("open_interest_below_min")

    spread_pct = _get_spread_pct(contract)
    if spread_pct is None:
        reasons.append("missing_spread_pct")
    elif spread_pct > HYBRID_MAX_SPREAD_PCT:
        reasons.append("spread_above_max")

    delta = _get_greek(contract, "delta")
    abs_delta = abs(delta) if delta is not None else None
    if abs_delta is None:
        reasons.append("missing_delta")
    elif abs_delta < HYBRID_MIN_ABS_DELTA:
        reasons.append("delta_below_min")

    theta_to_price = _get_theta_to_price(contract)
    if theta_to_price is None:
        reasons.append("missing_theta_to_price")
    elif theta_to_price > HYBRID_MAX_THETA_TO_PRICE:
        reasons.append("theta_to_price_above_max")

    gamma = _get_greek(contract, "gamma")
    if gamma is None:
        reasons.append("missing_gamma")
    elif gamma < HYBRID_MIN_GAMMA:
        reasons.append("gamma_below_min")

    return len(reasons) == 0, reasons


def _hybrid_fast_score(
    contract: dict[str, Any],
    *,
    normalized_decision: str,
    reference_stock_price: float | None,
) -> tuple[float, float, float, float, float, str, int]:
    strike_price = _coerce_float(contract.get("strike_price"))
    spread_pct = _get_spread_pct(contract) or 999.0
    open_interest = _coerce_float(contract.get("open_interest")) or 0.0
    abs_delta = abs(_get_greek(contract, "delta") or 0.0)
    gamma = _get_greek(contract, "gamma") or 0.0
    expiration_date = str(contract.get("expiration_date") or "9999-12-31")
    option_id = _normalize_option_id(contract.get("option_id")) or 10**9

    strike_distance_score = _distance_to_target_otm(
        normalized_decision,
        strike_price,
        reference_stock_price,
        target_distance=HYBRID_PREFERRED_OTM_DISTANCE,
    )

    return (
        strike_distance_score,
        spread_pct,
        abs(abs_delta - HYBRID_TARGET_ABS_DELTA),
        -open_interest,
        -gamma,
        expiration_date,
        option_id,
    )


def _pick_matching_contract_hybrid(
    *,
    decision: str,
    option_contracts: list[dict[str, Any]],
    market_context: dict[str, Any],
) -> dict[str, Any] | None:
    normalized_decision = _normalize_decision(decision)
    if normalized_decision not in {"call", "put"}:
        return None

    reference_stock_price = _get_reference_stock_price(market_context)

    matching_contracts = [
        contract
        for contract in option_contracts
        if _normalize_contract_type(contract.get("contract_type")) == normalized_decision
    ]
    if not matching_contracts:
        return None

    passed_contracts: list[dict[str, Any]] = []
    rejected_contracts_debug: list[dict[str, Any]] = []

    for contract in matching_contracts:
        passed, reasons = _passes_hybrid_fast_filters(
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

    if not passed_contracts:
        if not ALLOW_RISKY_SIMPLE_FALLBACK:
            return None
        fallback = _pick_matching_contract_simple(
            decision=decision,
            option_contracts=option_contracts,
            market_context=market_context,
        )
        if fallback is not None:
            fallback["_selection_mode"] = "hybrid_fallback_to_simple"
        return fallback

    candidate_pool = list(passed_contracts)
    candidate_pool.sort(
        key=lambda contract: _hybrid_fast_score(
            contract,
            normalized_decision=normalized_decision,
            reference_stock_price=reference_stock_price,
        )
    )

    selected = dict(candidate_pool[0])
    selected["_selection_mode"] = "hybrid_fast_short_dte"
    selected["_selector_debug"] = {
        "selector_mode_requested": OPTION_SELECTOR_MODE,
        "reference_stock_price": reference_stock_price,
        "passed_hybrid_count": len(passed_contracts),
        "rejected_hybrid_count": len(rejected_contracts_debug),
        "rejected_hybrid_examples": rejected_contracts_debug[:10],
        "candidate_pool_count": len(candidate_pool),
        "selected_contract_snapshot": _contract_debug_snapshot(
            selected,
            reference_stock_price=reference_stock_price,
        ),
    }
    return selected


# =========================
# GREEKS MODE
# =========================
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
    otm_distance_pref = _distance_to_target_otm(
        normalized_decision,
        strike_price,
        reference_stock_price,
        target_distance=PREFERRED_OTM_DISTANCE,
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


def _pick_matching_contract_greeks(
    *,
    decision: str,
    option_contracts: list[dict[str, Any]],
    market_context: dict[str, Any],
) -> dict[str, Any] | None:
    normalized_decision = _normalize_decision(decision)
    if normalized_decision not in {"call", "put"}:
        return None

    reference_stock_price = _get_reference_stock_price(market_context)

    matching_contracts = [
        contract
        for contract in option_contracts
        if _normalize_contract_type(contract.get("contract_type")) == normalized_decision
    ]

    LOGGER.info(
        "Greeks selector matched %s of %s contracts for decision=%s",
        len(matching_contracts),
        len(option_contracts),
        normalized_decision,
    )

    if not matching_contracts:
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
    elif ALLOW_RISKY_SIMPLE_FALLBACK:
        fallback = _pick_matching_contract_simple(
            decision=decision,
            option_contracts=option_contracts,
            market_context=market_context,
        )
        if fallback is None:
            return None
        fallback["_selection_mode"] = "greeks_fallback_to_simple"
        fallback["_selector_debug"] = {
            "selector_mode_requested": OPTION_SELECTOR_MODE,
            "reference_stock_price": reference_stock_price,
            "passed_short_swing_count": len(passed_contracts),
            "rejected_short_swing_count": len(rejected_contracts_debug),
            "rejected_short_swing_examples": rejected_contracts_debug[:10],
            "fallback_used": True,
            "fallback_mode": "simple",
            "selected_contract_snapshot": _contract_debug_snapshot(
                fallback,
                reference_stock_price=reference_stock_price,
            ),
        }
        return fallback
    else:
        return None

    selected = dict(candidate_pool[0])
    selected["_selection_mode"] = selection_mode
    selected["_selector_debug"] = {
        "selector_mode_requested": OPTION_SELECTOR_MODE,
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

    return selected


# =========================
# DISPATCHER
# =========================
def _pick_matching_contract(
    *,
    decision: str,
    option_contracts: list[dict[str, Any]],
    market_context: dict[str, Any],
) -> dict[str, Any] | None:
    if OPTION_SELECTOR_MODE == "simple":
        return _pick_matching_contract_simple(
            decision=decision,
            option_contracts=option_contracts,
            market_context=market_context,
        )

    if OPTION_SELECTOR_MODE == "hybrid":
        return _pick_matching_contract_hybrid(
            decision=decision,
            option_contracts=option_contracts,
            market_context=market_context,
        )

    if OPTION_SELECTOR_MODE == "greeks":
        return _pick_matching_contract_greeks(
            decision=decision,
            option_contracts=option_contracts,
            market_context=market_context,
        )

    LOGGER.warning(
        "Unknown OPTION_SELECTOR_MODE=%s; defaulting to hybrid.",
        OPTION_SELECTOR_MODE,
    )
    return _pick_matching_contract_hybrid(
        decision=decision,
        option_contracts=option_contracts,
        market_context=market_context,
    )


def apply_deterministic_option_selection(manager_result: dict[str, Any]) -> dict[str, Any]:
    """Attach a concrete option contract to a manager result when rules allow it."""
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
            "option_selector_mode": OPTION_SELECTOR_MODE,
            "allow_risky_simple_fallback": ALLOW_RISKY_SIMPLE_FALLBACK,
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
            "simple_thresholds": {
                "require_one_dollar_otm": SIMPLE_REQUIRE_ONE_DOLLAR_OTM,
                "preferred_otm_distance": SIMPLE_PREFERRED_OTM_DISTANCE,
            },
            "hybrid_thresholds": {
                "min_dte": HYBRID_MIN_DTE,
                "max_dte": HYBRID_MAX_DTE,
                "min_open_interest": HYBRID_MIN_OPEN_INTEREST,
                "max_spread_pct": HYBRID_MAX_SPREAD_PCT,
                "min_abs_delta": HYBRID_MIN_ABS_DELTA,
                "target_abs_delta": HYBRID_TARGET_ABS_DELTA,
                "min_gamma": HYBRID_MIN_GAMMA,
                "max_theta_to_price": HYBRID_MAX_THETA_TO_PRICE,
                "require_min_otm_distance": HYBRID_REQUIRE_MIN_OTM_DISTANCE,
                "preferred_otm_distance": HYBRID_PREFERRED_OTM_DISTANCE,
            },
            "greeks_thresholds": {
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
