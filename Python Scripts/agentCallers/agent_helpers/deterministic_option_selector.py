from __future__ import annotations

"""Deterministically choose an option contract after the manager stage decides direction."""

import copy
import json
import logging
from pathlib import Path
import sys
from typing import Any


SELECTOR_VERSION = "deterministic-selector-v6-volatility-scoring"

LOGGER = logging.getLogger(__name__)

if __package__ in {None, ""}:
    AGENT_CALLERS_DIR = Path(__file__).resolve().parents[1]
    if str(AGENT_CALLERS_DIR) not in sys.path:
        sys.path.append(str(AGENT_CALLERS_DIR))

from _paths import bootstrap_agent_callers


bootstrap_agent_callers()


from agent_helpers._selector_config import (  # noqa: E402
    ALLOW_RISKY_SIMPLE_FALLBACK,
    DTE_BUCKET_TO_TARGET_OTM_PCT,
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
    MAX_ABS_DELTA,
    MAX_DTE,
    MAX_SPREAD_PCT,
    MAX_THETA_TO_PRICE,
    MIN_ABS_DELTA,
    MIN_DTE,
    MIN_GAMMA,
    MIN_OPEN_INTEREST,
    OPTION_SELECTOR_MODE,
    PREFERRED_OTM_DISTANCE,
    REQUIRE_ONE_DOLLAR_OTM,
    SIMPLE_PREFERRED_OTM_DISTANCE,
    SIMPLE_REQUIRE_ONE_DOLLAR_OTM,
    SIMPLE_TARGET_ABS_DELTA,
    TARGET_ABS_DELTA,
    VOLATILITY_CONFIDENCE_LOWEST_ALLOWED,
    MIN_TARGET_OTM_DOLLARS,
    MAX_TARGET_OTM_DOLLARS,
)
from agent_helpers._selector_debug import _contract_debug_snapshot  # noqa: E402
from agent_helpers._selector_filters import (  # noqa: E402
    _is_one_dollar_otm_contract,
    _is_valid_fallback_side_contract,
)
from agent_helpers._selector_greeks import _pick_matching_contract_greeks  # noqa: E402
from agent_helpers._selector_hybrid import _pick_matching_contract_hybrid  # noqa: E402
from agent_helpers._selector_market import (  # noqa: E402
    _get_reference_stock_price,
    _normalize_contract_type,
    _resolve_target_otm_distance,
)
from agent_helpers._selector_normalize import (  # noqa: E402
    _coerce_bool,
    _coerce_float,
    _confidence_rank,
    _downgrade_confidence,
    _is_selection_eligible_under_confidence_guardrails,
    _normalize_confidence,
    _normalize_decision,
    _normalize_option_id,
    _normalize_strategist_decision,
)
from agent_helpers._selector_simple import _pick_matching_contract_simple  # noqa: E402
from agent_helpers._selector_volatility import _assess_contract_volatility  # noqa: E402
from services.option_dte_buckets import normalize_target_dte_bucket  # noqa: E402


def _pick_matching_contract(
    *,
    decision: str,
    option_contracts: list[dict[str, Any]],
    market_context: dict[str, Any],
    target_dte_bucket: str = "none",
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if OPTION_SELECTOR_MODE == "simple":
        return _pick_matching_contract_simple(
            decision=decision,
            option_contracts=option_contracts,
            market_context=market_context,
            target_dte_bucket=target_dte_bucket,
            selector_mode_requested=OPTION_SELECTOR_MODE,
        )
    if OPTION_SELECTOR_MODE == "hybrid":
        return _pick_matching_contract_hybrid(
            decision=decision,
            option_contracts=option_contracts,
            market_context=market_context,
            target_dte_bucket=target_dte_bucket,
            selector_mode_requested=OPTION_SELECTOR_MODE,
            allow_risky_simple_fallback=ALLOW_RISKY_SIMPLE_FALLBACK,
            simple_picker=_pick_matching_contract_simple,
        )
    if OPTION_SELECTOR_MODE == "greeks":
        return _pick_matching_contract_greeks(
            decision=decision,
            option_contracts=option_contracts,
            market_context=market_context,
            target_dte_bucket=target_dte_bucket,
            selector_mode_requested=OPTION_SELECTOR_MODE,
            allow_risky_simple_fallback=ALLOW_RISKY_SIMPLE_FALLBACK,
            simple_picker=_pick_matching_contract_simple,
        )

    LOGGER.warning("Unknown OPTION_SELECTOR_MODE=%s; defaulting to hybrid.", OPTION_SELECTOR_MODE)
    return _pick_matching_contract_hybrid(
        decision=decision,
        option_contracts=option_contracts,
        market_context=market_context,
        target_dte_bucket=target_dte_bucket,
        selector_mode_requested=OPTION_SELECTOR_MODE,
        allow_risky_simple_fallback=ALLOW_RISKY_SIMPLE_FALLBACK,
        simple_picker=_pick_matching_contract_simple,
    )


def apply_deterministic_option_selection(manager_result: dict[str, Any]) -> dict[str, Any]:
    """Attach a concrete option contract to a manager result when rules allow it."""
    recommendation = dict(manager_result.get("recommendation") or {})
    market_context = dict(manager_result.get("market_context") or {})
    strategist_recommendation = dict(manager_result.get("strategist_recommendation") or {})
    option_market = dict(market_context.get("option_market") or {})
    option_contracts = list(option_market.get("contracts") or [])

    decision = _normalize_decision(recommendation.get("decision"))
    confidence = _normalize_confidence(recommendation.get("confidence"))
    target_dte_bucket = normalize_target_dte_bucket(recommendation.get("target_dte_bucket")) or "none"
    selection_filters = dict(option_market.get("selection_filters") or {})
    reference_stock_price = _get_reference_stock_price(market_context)
    resolved_target_otm_distance = _resolve_target_otm_distance(
        reference_stock_price=reference_stock_price,
        target_dte_bucket=target_dte_bucket,
        default_distance=(
            SIMPLE_PREFERRED_OTM_DISTANCE
            if OPTION_SELECTOR_MODE == "simple"
            else HYBRID_PREFERRED_OTM_DISTANCE
            if OPTION_SELECTOR_MODE == "hybrid"
            else PREFERRED_OTM_DISTANCE
        ),
    )

    selected_option: dict[str, Any] | None = None
    selected_option_source = "not_applicable"
    selection_mode = ""
    selector_debug: dict[str, Any] = {}
    selected_option_volatility_assessment: dict[str, Any] = {}
    confidence_after_volatility = confidence
    volatility_confidence_penalty_steps = 0
    volatility_guardrail_reason = ""
    selection_allowed, selection_guardrail_reason = _is_selection_eligible_under_confidence_guardrails(
        decision=decision,
        confidence=confidence,
        strategist_recommendation=strategist_recommendation,
    )
    selection_allowed_after_volatility = selection_allowed

    if selection_allowed:
        selected_option, selector_debug = _pick_matching_contract(
            decision=decision,
            option_contracts=option_contracts,
            market_context=market_context,
            target_dte_bucket=target_dte_bucket,
        )
        if selected_option is not None:
            selection_mode = str(selected_option.get("_selection_mode") or "")
            selector_debug = dict(selected_option.get("_selector_debug") or selector_debug)
            selected_option = {
                key: value
                for key, value in selected_option.items()
                if key not in {"_selection_mode", "_selector_debug"}
            }
        else:
            selection_mode = str(selector_debug.get("selection_mode") or "")

        selected_option_source = (
            f"deterministic_{confidence}_confidence_{selection_mode}"
            if selected_option is not None and selection_mode
            else "unavailable"
        )
    elif decision in {"call", "put"}:
        selected_option_source = selection_guardrail_reason or "confidence_below_medium"

    if selected_option is not None:
        selected_option_volatility_assessment = _assess_contract_volatility(
            selected_option,
            market_context=market_context,
            target_dte_bucket=target_dte_bucket,
        )
        volatility_confidence_penalty_steps = int(
            selected_option_volatility_assessment.get("confidence_penalty_steps") or 0
        )
        confidence_after_volatility = _downgrade_confidence(
            confidence,
            volatility_confidence_penalty_steps,
        )
        if _confidence_rank(confidence_after_volatility) < _confidence_rank(
            VOLATILITY_CONFIDENCE_LOWEST_ALLOWED
        ):
            selection_allowed_after_volatility = False
            volatility_guardrail_reason = "volatility_confidence_below_medium"
            selected_option = None
            selected_option_source = volatility_guardrail_reason
            selection_mode = f"{selection_mode}_volatility_rejected" if selection_mode else "volatility_rejected"
        else:
            selection_allowed_after_volatility = True
            if selection_mode:
                selected_option_source = (
                    f"deterministic_{confidence_after_volatility}_confidence_{selection_mode}"
                )

    selected_option_id = _normalize_option_id(selected_option.get("option_id")) if selected_option else None
    selected_expiration_date = (
        str(selected_option.get("expiration_date") or "").strip() or None
        if selected_option
        else None
    )
    selected_strike_price = _coerce_float(selected_option.get("strike_price")) if selected_option else None

    updated_recommendation = {
        **recommendation,
        "confidence": confidence or recommendation.get("confidence"),
        "confidence_after_volatility": confidence_after_volatility or confidence or recommendation.get("confidence"),
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
            "confidence_after_volatility": confidence_after_volatility,
            "volatility_confidence_penalty_steps": volatility_confidence_penalty_steps,
            "selection_allowed": selection_allowed,
            "selection_allowed_after_volatility": selection_allowed_after_volatility,
            "selection_guardrail_reason": selection_guardrail_reason,
            "volatility_guardrail_reason": volatility_guardrail_reason,
            "strategist_decision_seen": _normalize_strategist_decision(
                strategist_recommendation.get("decision")
            ),
            "strategist_contradictions_present_seen": _coerce_bool(
                strategist_recommendation.get("contradictions_present")
            ),
            "target_dte_bucket_seen": target_dte_bucket,
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
                    target_otm_distance=resolved_target_otm_distance,
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
                "target_abs_delta": SIMPLE_TARGET_ABS_DELTA,
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
            "dynamic_target_otm": {
                "bucket_to_target_otm_pct": DTE_BUCKET_TO_TARGET_OTM_PCT,
                "min_otm_dollars": MIN_TARGET_OTM_DOLLARS,
                "max_otm_dollars": MAX_TARGET_OTM_DOLLARS,
                "resolved_target_dte_bucket": target_dte_bucket,
                "resolved_target_otm_distance": resolved_target_otm_distance,
            },
            "selection_mode": selection_mode,
            "selected_option_volatility_assessment": selected_option_volatility_assessment,
            "selected_option_id": selected_option_id,
            "selected_option_symbol": str(selected_option.get("symbol") or "").strip() if selected_option else "",
            "selected_option_strike_price": selected_strike_price,
            "selected_option_expiration_date": selected_expiration_date,
            "selected_option_snapshot": (
                _contract_debug_snapshot(
                    selected_option,
                    reference_stock_price=reference_stock_price,
                    market_context=market_context,
                    target_dte_bucket=target_dte_bucket,
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
