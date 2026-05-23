from __future__ import annotations

from typing import Any

from .config import (
    DTE_BUCKET_TO_TARGET_OTM_PCT,
    SIMPLE_PREFERRED_OTM_DISTANCE,
    SIMPLE_REQUIRE_ONE_DOLLAR_OTM,
    SIMPLE_TARGET_ABS_DELTA,
)
from .debug import (
    _build_simple_rejection_reasons,
    _contract_debug_snapshot,
    _debug_contract_with_reasons,
)
from .filters import (
    _basic_sort_key,
    _contract_matches_target_dte_bucket,
    _is_one_dollar_otm_contract,
    _is_otm_contract,
    _meets_min_otm_distance,
)
from .market import (
    _get_reference_stock_price,
    _normalize_contract_type,
    _resolve_target_otm_distance,
)
from .normalize import _coerce_float, _normalize_decision
from services.option_dte_buckets import normalize_target_dte_bucket


def _pick_matching_contract_simple(
    *,
    decision: str,
    option_contracts: list[dict[str, Any]],
    market_context: dict[str, Any],
    target_dte_bucket: str = "none",
    selector_mode_requested: str = "simple",
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    normalized_decision = _normalize_decision(decision)
    if normalized_decision not in {"call", "put"}:
        return None, {}

    normalized_target_dte_bucket = normalize_target_dte_bucket(target_dte_bucket) or "none"
    reference_stock_price = _get_reference_stock_price(market_context)
    target_otm_distance = _resolve_target_otm_distance(
        reference_stock_price=reference_stock_price,
        target_dte_bucket=normalized_target_dte_bucket,
        default_distance=SIMPLE_PREFERRED_OTM_DISTANCE,
    )

    bucket_matching_contracts = [
        contract
        for contract in option_contracts
        if _normalize_contract_type(contract.get("contract_type")) == normalized_decision
        and _contract_matches_target_dte_bucket(contract, normalized_target_dte_bucket)
    ]
    matching_contracts = list(bucket_matching_contracts) or [
        contract
        for contract in option_contracts
        if _normalize_contract_type(contract.get("contract_type")) == normalized_decision
    ]
    if not matching_contracts:
        return None, {
            "selector_mode_requested": selector_mode_requested,
            "target_dte_bucket": normalized_target_dte_bucket,
            "target_otm_distance": target_otm_distance,
            "reference_stock_price": reference_stock_price,
            "matching_contract_count": 0,
            "bucket_matching_contract_count": 0,
            "preferred_contract_count": 0,
            "fallback_contract_count": 0,
            "candidate_pool_count": 0,
            "selection_mode": "simple_no_matching_contracts",
            "rejected_simple_count": 0,
            "rejected_simple_examples": [],
        }

    preferred_contracts = [
        contract
        for contract in matching_contracts
        if (
            _is_one_dollar_otm_contract(
                normalized_decision,
                _coerce_float(contract.get("strike_price")),
                reference_stock_price,
                target_otm_distance=target_otm_distance,
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
        if (
            _meets_min_otm_distance(
                normalized_decision,
                _coerce_float(contract.get("strike_price")),
                reference_stock_price,
                min_distance=target_otm_distance,
            )
            if normalized_target_dte_bucket in DTE_BUCKET_TO_TARGET_OTM_PCT
            else _is_otm_contract(
                normalized_decision,
                _coerce_float(contract.get("strike_price")),
                reference_stock_price,
            )
        )
    ]
    rejected_contracts_debug = [
        _debug_contract_with_reasons(
            contract,
            reference_stock_price=reference_stock_price,
            market_context=market_context,
            target_otm_distance=target_otm_distance,
            target_dte_bucket=normalized_target_dte_bucket,
            rejection_reasons=_build_simple_rejection_reasons(
                contract,
                normalized_decision=normalized_decision,
                reference_stock_price=reference_stock_price,
                target_otm_distance=target_otm_distance,
                normalized_target_dte_bucket=normalized_target_dte_bucket,
            ),
        )
        for contract in matching_contracts
        if contract not in fallback_contracts
    ]

    if preferred_contracts:
        candidate_pool = list(preferred_contracts)
        selection_mode = "simple_preferred_near_otm"
        candidate_pool.sort(
            key=lambda contract: _basic_sort_key(
                contract,
                normalized_decision=normalized_decision,
                reference_stock_price=reference_stock_price,
                market_context=market_context,
                prefer_target_otm=True,
                target_distance=target_otm_distance,
                target_abs_delta=SIMPLE_TARGET_ABS_DELTA,
                target_dte_bucket=normalized_target_dte_bucket,
            )
        )
    elif fallback_contracts:
        candidate_pool = list(fallback_contracts)
        selection_mode = (
            "simple_fallback_min_distance_bucketed"
            if normalized_target_dte_bucket in DTE_BUCKET_TO_TARGET_OTM_PCT
            else "simple_fallback_closest_side_correct"
        )
        candidate_pool.sort(
            key=lambda contract: _basic_sort_key(
                contract,
                normalized_decision=normalized_decision,
                reference_stock_price=reference_stock_price,
                market_context=market_context,
                prefer_target_otm=False,
                target_distance=target_otm_distance,
                target_abs_delta=SIMPLE_TARGET_ABS_DELTA,
                target_dte_bucket=normalized_target_dte_bucket,
            )
        )
    else:
        return None, {
            "selector_mode_requested": selector_mode_requested,
            "target_dte_bucket": normalized_target_dte_bucket,
            "target_otm_distance": target_otm_distance,
            "reference_stock_price": reference_stock_price,
            "matching_contract_count": len(matching_contracts),
            "bucket_matching_contract_count": len(bucket_matching_contracts),
            "used_direction_only_fallback": len(bucket_matching_contracts) == 0 and len(matching_contracts) > 0,
            "preferred_contract_count": len(preferred_contracts),
            "fallback_contract_count": len(fallback_contracts),
            "candidate_pool_count": 0,
            "selection_mode": "simple_no_candidate_pool",
            "rejected_simple_count": len(rejected_contracts_debug),
            "rejected_simple_examples": rejected_contracts_debug[:10],
        }

    selected = dict(candidate_pool[0])
    selected["_selection_mode"] = selection_mode
    selected["_selector_debug"] = {
        "selector_mode_requested": selector_mode_requested,
        "target_dte_bucket": normalized_target_dte_bucket,
        "target_otm_distance": target_otm_distance,
        "reference_stock_price": reference_stock_price,
        "matching_contract_count": len(matching_contracts),
        "bucket_matching_contract_count": len(bucket_matching_contracts),
        "used_direction_only_fallback": len(bucket_matching_contracts) == 0 and len(matching_contracts) > 0,
        "preferred_contract_count": len(preferred_contracts),
        "fallback_contract_count": len(fallback_contracts),
        "candidate_pool_count": len(candidate_pool),
        "rejected_simple_count": len(rejected_contracts_debug),
        "rejected_simple_examples": rejected_contracts_debug[:10],
        "selection_mode": selection_mode,
        "selected_contract_snapshot": _contract_debug_snapshot(
            selected,
            reference_stock_price=reference_stock_price,
            market_context=market_context,
            target_otm_distance=target_otm_distance,
            target_dte_bucket=normalized_target_dte_bucket,
        ),
    }
    return selected, dict(selected["_selector_debug"])
