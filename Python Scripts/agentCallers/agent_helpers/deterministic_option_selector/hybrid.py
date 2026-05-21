from __future__ import annotations

from typing import Any, Callable

from .config import (
    ALLOW_RISKY_SIMPLE_FALLBACK,
    HYBRID_MAX_DTE,
    HYBRID_MAX_SPREAD_PCT,
    HYBRID_MAX_THETA_TO_PRICE,
    HYBRID_MIN_ABS_DELTA,
    HYBRID_MIN_DTE,
    HYBRID_MIN_GAMMA,
    HYBRID_MIN_OPEN_INTEREST,
    HYBRID_PREFERRED_OTM_DISTANCE,
    HYBRID_REQUIRE_MIN_OTM_DISTANCE,
    HYBRID_TARGET_ABS_DELTA,
)
from .debug import _contract_debug_snapshot, _debug_contract_with_reasons
from .filters import (
    _get_spread_pct,
    _get_theta_to_price,
    _is_valid_fallback_side_contract,
    _meets_min_otm_distance,
    _resolve_dte_range,
    _distance_to_target_otm,
)
from .market import (
    _get_dte,
    _get_greek,
    _get_reference_stock_price,
    _normalize_contract_type,
    _resolve_target_otm_distance,
)
from .normalize import _coerce_float, _normalize_decision, _normalize_option_id
from .volatility import _assess_contract_volatility


def _passes_hybrid_fast_filters(
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
        default_distance=HYBRID_PREFERRED_OTM_DISTANCE,
    )
    strike_price = _coerce_float(contract.get("strike_price"))
    if strike_price is None:
        return False, ["missing_strike_price"]
    if not _is_valid_fallback_side_contract(normalized_decision, strike_price, reference_stock_price):
        return False, ["wrong_side"]
    if HYBRID_REQUIRE_MIN_OTM_DISTANCE and not _meets_min_otm_distance(
        normalized_decision,
        strike_price,
        reference_stock_price,
        min_distance=target_otm_distance,
    ):
        return False, ["otm_distance_below_preferred_min"]

    dte = _get_dte(contract)
    effective_min_dte, effective_max_dte = _resolve_dte_range(
        target_dte_bucket=target_dte_bucket,
        default_min_dte=HYBRID_MIN_DTE,
        default_max_dte=HYBRID_MAX_DTE,
    )
    if dte is None:
        reasons.append("missing_dte")
    elif dte < effective_min_dte:
        reasons.append("dte_below_min")
    elif dte > effective_max_dte:
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
    market_context: dict[str, Any] | None = None,
    target_dte_bucket: str = "none",
) -> tuple[float, float, float, float, float, float, float, str, int]:
    target_otm_distance = _resolve_target_otm_distance(
        reference_stock_price=reference_stock_price,
        target_dte_bucket=target_dte_bucket,
        default_distance=HYBRID_PREFERRED_OTM_DISTANCE,
    )
    volatility_assessment = _assess_contract_volatility(
        contract,
        market_context=market_context or {},
        target_dte_bucket=target_dte_bucket,
    )
    return (
        float(volatility_assessment.get("core_penalty_points", volatility_assessment["total_penalty_points"])),
        _distance_to_target_otm(
            normalized_decision,
            _coerce_float(contract.get("strike_price")),
            reference_stock_price,
            target_distance=target_otm_distance,
        ),
        _get_spread_pct(contract) or 999.0,
        abs(abs(_get_greek(contract, "delta") or 0.0) - HYBRID_TARGET_ABS_DELTA),
        -(_coerce_float(contract.get("open_interest")) or 0.0),
        -(_get_greek(contract, "gamma") or 0.0),
        float(volatility_assessment.get("selection_preference_score", 0.0)),
        str(contract.get("expiration_date") or "9999-12-31"),
        _normalize_option_id(contract.get("option_id")) or 10**9,
    )


def _pick_matching_contract_hybrid(
    *,
    decision: str,
    option_contracts: list[dict[str, Any]],
    market_context: dict[str, Any],
    target_dte_bucket: str = "none",
    selector_mode_requested: str = "hybrid",
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
        default_distance=HYBRID_PREFERRED_OTM_DISTANCE,
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
            "passed_hybrid_count": 0,
            "rejected_hybrid_count": 0,
            "rejected_hybrid_examples": [],
            "candidate_pool_count": 0,
            "selection_mode": "hybrid_no_matching_contracts",
        }

    passed_contracts: list[dict[str, Any]] = []
    rejected_contracts_debug: list[dict[str, Any]] = []
    for contract in matching_contracts:
        passed, reasons = _passes_hybrid_fast_filters(
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

    if not passed_contracts:
        if not allow_risky_simple_fallback or simple_picker is None:
            return None, {
                "selector_mode_requested": selector_mode_requested,
                "target_dte_bucket": target_dte_bucket,
                "target_otm_distance": target_otm_distance,
                "reference_stock_price": reference_stock_price,
                "passed_hybrid_count": 0,
                "rejected_hybrid_count": len(rejected_contracts_debug),
                "rejected_hybrid_examples": rejected_contracts_debug[:10],
                "candidate_pool_count": 0,
                "selection_mode": "hybrid_no_candidate_pool",
                "fallback_used": False,
                "fallback_mode": "",
            }
        fallback, fallback_debug = simple_picker(
            decision=decision,
            option_contracts=option_contracts,
            market_context=market_context,
            target_dte_bucket=target_dte_bucket,
            selector_mode_requested=selector_mode_requested,
        )
        if fallback is not None:
            fallback["_selection_mode"] = "hybrid_fallback_to_simple"
            fallback["_selector_debug"] = {
                **fallback_debug,
                "selector_mode_requested": selector_mode_requested,
                "target_dte_bucket": target_dte_bucket,
                "target_otm_distance": target_otm_distance,
                "reference_stock_price": reference_stock_price,
                "passed_hybrid_count": 0,
                "rejected_hybrid_count": len(rejected_contracts_debug),
                "rejected_hybrid_examples": rejected_contracts_debug[:10],
                "candidate_pool_count": 1,
                "selection_mode": "hybrid_fallback_to_simple",
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
        return None, {
            "selector_mode_requested": selector_mode_requested,
            "target_dte_bucket": target_dte_bucket,
            "target_otm_distance": target_otm_distance,
            "reference_stock_price": reference_stock_price,
            "passed_hybrid_count": 0,
            "rejected_hybrid_count": len(rejected_contracts_debug),
            "rejected_hybrid_examples": rejected_contracts_debug[:10],
            "candidate_pool_count": 0,
            "selection_mode": "hybrid_fallback_simple_no_candidate_pool",
            "fallback_used": True,
            "fallback_mode": "simple",
            "fallback_selector_debug": fallback_debug,
        }

    candidate_pool = list(passed_contracts)
    candidate_pool.sort(
        key=lambda contract: _hybrid_fast_score(
            contract,
            normalized_decision=normalized_decision,
            reference_stock_price=reference_stock_price,
            market_context=market_context,
            target_dte_bucket=target_dte_bucket,
        )
    )
    selected = dict(candidate_pool[0])
    selected["_selection_mode"] = "hybrid_fast_short_dte"
    selected["_selector_debug"] = {
        "selector_mode_requested": selector_mode_requested,
        "target_dte_bucket": target_dte_bucket,
        "target_otm_distance": target_otm_distance,
        "reference_stock_price": reference_stock_price,
        "passed_hybrid_count": len(passed_contracts),
        "rejected_hybrid_count": len(rejected_contracts_debug),
        "rejected_hybrid_examples": rejected_contracts_debug[:10],
        "candidate_pool_count": len(candidate_pool),
        "selected_contract_snapshot": _contract_debug_snapshot(
            selected,
            reference_stock_price=reference_stock_price,
            market_context=market_context,
            target_otm_distance=target_otm_distance,
            target_dte_bucket=target_dte_bucket,
        ),
    }
    return selected, dict(selected["_selector_debug"])
