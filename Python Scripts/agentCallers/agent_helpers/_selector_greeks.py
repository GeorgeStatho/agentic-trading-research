from __future__ import annotations

from typing import Any, Callable

from agent_helpers._selector_config import (
    ALLOW_RISKY_SIMPLE_FALLBACK,
    MAX_ABS_DELTA,
    MAX_DTE,
    MAX_SPREAD_PCT,
    MAX_THETA_TO_PRICE,
    MIN_ABS_DELTA,
    MIN_DTE,
    MIN_GAMMA,
    MIN_OPEN_INTEREST,
    PREFERRED_OTM_DISTANCE,
    REQUIRE_ONE_DOLLAR_OTM,
    TARGET_ABS_DELTA,
)
from agent_helpers._selector_debug import _contract_debug_snapshot, _debug_contract_with_reasons
from agent_helpers._selector_filters import (
    _distance_to_target_otm,
    _get_spread_pct,
    _get_theta_to_price,
    _is_one_dollar_otm_contract,
    _is_otm_contract,
    _resolve_dte_range,
)
from agent_helpers._selector_market import (
    _get_dte,
    _get_greek,
    _get_reference_stock_price,
    _normalize_contract_type,
    _resolve_target_otm_distance,
)
from agent_helpers._selector_normalize import _coerce_float, _normalize_decision, _normalize_option_id
from agent_helpers._selector_volatility import _assess_contract_volatility


def _passes_short_swing_filters(
    contract: dict[str, Any],
    *,
    normalized_decision: str,
    reference_stock_price: float | None,
    target_dte_bucket: str = "none",
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    target_otm_distance = _resolve_target_otm_distance(
        reference_stock_price=reference_stock_price,
        target_dte_bucket=target_dte_bucket,
        default_distance=PREFERRED_OTM_DISTANCE,
    )
    strike_price = _coerce_float(contract.get("strike_price"))
    if strike_price is None:
        return False, ["missing_strike_price"]

    if REQUIRE_ONE_DOLLAR_OTM:
        if not _is_one_dollar_otm_contract(
            normalized_decision,
            strike_price,
            reference_stock_price,
            target_otm_distance=target_otm_distance,
        ):
            reasons.append("not_one_dollar_otm")
    elif not _is_otm_contract(normalized_decision, strike_price, reference_stock_price):
        reasons.append("not_otm")

    dte = _get_dte(contract)
    effective_min_dte, effective_max_dte = _resolve_dte_range(
        target_dte_bucket=target_dte_bucket,
        default_min_dte=MIN_DTE,
        default_max_dte=MAX_DTE,
    )
    if dte is None:
        reasons.append("missing_dte")
    elif dte < effective_min_dte:
        reasons.append("dte_below_min")
    elif dte > effective_max_dte:
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
    market_context: dict[str, Any] | None = None,
    target_dte_bucket: str = "none",
) -> tuple[float, float, float, float, float, float, float, str, int]:
    target_otm_distance = _resolve_target_otm_distance(
        reference_stock_price=reference_stock_price,
        target_dte_bucket=target_dte_bucket,
        default_distance=PREFERRED_OTM_DISTANCE,
    )
    volatility_assessment = _assess_contract_volatility(
        contract,
        market_context=market_context or {},
        target_dte_bucket=target_dte_bucket,
    )
    return (
        float(volatility_assessment["total_penalty_points"]),
        abs(abs(_get_greek(contract, "delta") or 0.0) - TARGET_ABS_DELTA),
        _get_spread_pct(contract) or 999.0,
        _get_theta_to_price(contract) or 999.0,
        -(_get_greek(contract, "gamma") or 0.0),
        -(_coerce_float(contract.get("open_interest")) or 0.0),
        _distance_to_target_otm(
            normalized_decision,
            _coerce_float(contract.get("strike_price")),
            reference_stock_price,
            target_distance=target_otm_distance,
        ),
        str(contract.get("expiration_date") or "9999-12-31"),
        _normalize_option_id(contract.get("option_id")) or 10**9,
    )


def _pick_matching_contract_greeks(
    *,
    decision: str,
    option_contracts: list[dict[str, Any]],
    market_context: dict[str, Any],
    target_dte_bucket: str = "none",
    selector_mode_requested: str = "greeks",
    allow_risky_simple_fallback: bool = ALLOW_RISKY_SIMPLE_FALLBACK,
    simple_picker: Callable[..., tuple[dict[str, Any] | None, dict[str, Any]]] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    normalized_decision = _normalize_decision(decision)
    if normalized_decision not in {"call", "put"}:
        return None, {}

    reference_stock_price = _get_reference_stock_price(market_context)
    target_otm_distance = _resolve_target_otm_distance(
        reference_stock_price=reference_stock_price,
        target_dte_bucket=target_dte_bucket,
        default_distance=PREFERRED_OTM_DISTANCE,
    )
    matching_contracts = [
        contract
        for contract in option_contracts
        if _normalize_contract_type(contract.get("contract_type")) == normalized_decision
    ]
    if not matching_contracts:
        return None, {
            "selector_mode_requested": selector_mode_requested,
            "target_dte_bucket": target_dte_bucket,
            "target_otm_distance": target_otm_distance,
            "reference_stock_price": reference_stock_price,
            "passed_short_swing_count": 0,
            "rejected_short_swing_count": 0,
            "rejected_short_swing_examples": [],
            "candidate_pool_count": 0,
            "selection_mode": "greeks_no_matching_contracts",
        }

    passed_contracts: list[dict[str, Any]] = []
    rejected_contracts_debug: list[dict[str, Any]] = []
    for contract in matching_contracts:
        passed, reasons = _passes_short_swing_filters(
            contract,
            normalized_decision=normalized_decision,
            reference_stock_price=reference_stock_price,
            target_dte_bucket=target_dte_bucket,
        )
        if passed:
            passed_contracts.append(contract)
        else:
            rejected_contracts_debug.append(
                _debug_contract_with_reasons(
                    contract,
                    reference_stock_price=reference_stock_price,
                    market_context=market_context,
                    target_dte_bucket=target_dte_bucket,
                    rejection_reasons=reasons,
                )
            )

    if passed_contracts:
        candidate_pool = list(passed_contracts)
        selection_mode = "greeks_filtered_short_swing"
        candidate_pool.sort(
            key=lambda contract: _short_swing_score(
                contract,
                normalized_decision=normalized_decision,
                reference_stock_price=reference_stock_price,
                market_context=market_context,
                target_dte_bucket=target_dte_bucket,
            )
        )
    elif allow_risky_simple_fallback and simple_picker is not None:
        fallback, fallback_debug = simple_picker(
            decision=decision,
            option_contracts=option_contracts,
            market_context=market_context,
            target_dte_bucket=target_dte_bucket,
            selector_mode_requested=selector_mode_requested,
        )
        if fallback is None:
            return None, {
                "selector_mode_requested": selector_mode_requested,
                "target_dte_bucket": target_dte_bucket,
                "target_otm_distance": target_otm_distance,
                "reference_stock_price": reference_stock_price,
                "passed_short_swing_count": len(passed_contracts),
                "rejected_short_swing_count": len(rejected_contracts_debug),
                "rejected_short_swing_examples": rejected_contracts_debug[:10],
                "candidate_pool_count": 0,
                "selection_mode": "greeks_fallback_simple_no_candidate_pool",
                "fallback_used": True,
                "fallback_mode": "simple",
                "fallback_selector_debug": fallback_debug,
            }
        fallback["_selection_mode"] = "greeks_fallback_to_simple"
        fallback["_selector_debug"] = {
            **fallback_debug,
            "selector_mode_requested": selector_mode_requested,
            "target_dte_bucket": target_dte_bucket,
            "target_otm_distance": target_otm_distance,
            "reference_stock_price": reference_stock_price,
            "passed_short_swing_count": len(passed_contracts),
            "rejected_short_swing_count": len(rejected_contracts_debug),
            "rejected_short_swing_examples": rejected_contracts_debug[:10],
            "fallback_used": True,
            "fallback_mode": "simple",
            "fallback_selector_debug": fallback_debug,
            "selected_contract_snapshot": _contract_debug_snapshot(
                fallback,
                reference_stock_price=reference_stock_price,
                market_context=market_context,
                target_otm_distance=target_otm_distance,
                target_dte_bucket=target_dte_bucket,
            ),
        }
        return fallback, dict(fallback["_selector_debug"])
    else:
        return None, {
            "selector_mode_requested": selector_mode_requested,
            "target_dte_bucket": target_dte_bucket,
            "target_otm_distance": target_otm_distance,
            "reference_stock_price": reference_stock_price,
            "passed_short_swing_count": len(passed_contracts),
            "rejected_short_swing_count": len(rejected_contracts_debug),
            "rejected_short_swing_examples": rejected_contracts_debug[:10],
            "candidate_pool_count": 0,
            "selection_mode": "greeks_no_candidate_pool",
            "fallback_used": False,
            "fallback_mode": "",
        }

    selected = dict(candidate_pool[0])
    selected["_selection_mode"] = selection_mode
    selected["_selector_debug"] = {
        "selector_mode_requested": selector_mode_requested,
        "target_dte_bucket": target_dte_bucket,
        "target_otm_distance": target_otm_distance,
        "reference_stock_price": reference_stock_price,
        "passed_short_swing_count": len(passed_contracts),
        "rejected_short_swing_count": len(rejected_contracts_debug),
        "rejected_short_swing_examples": rejected_contracts_debug[:10],
        "candidate_pool_count": len(candidate_pool),
        "selection_mode": selection_mode,
        "selected_contract_snapshot": _contract_debug_snapshot(
            selected,
            reference_stock_price=reference_stock_price,
            market_context=market_context,
            target_otm_distance=target_otm_distance,
            target_dte_bucket=target_dte_bucket,
        ),
    }
    return selected, dict(selected["_selector_debug"])
