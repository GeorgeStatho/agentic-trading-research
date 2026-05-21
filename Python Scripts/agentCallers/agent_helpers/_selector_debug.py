from __future__ import annotations

from typing import Any

from agent_helpers._selector_config import DTE_BUCKET_TO_TARGET_OTM_PCT, SIMPLE_REQUIRE_ONE_DOLLAR_OTM
from agent_helpers._selector_filters import (
    _get_spread_pct,
    _get_theta_to_price,
    _is_one_dollar_otm_contract,
    _is_otm_contract,
    _meets_min_otm_distance,
    _otm_distance,
)
from agent_helpers._selector_market import (
    _get_ask_price,
    _get_bid_price,
    _get_contract_implied_volatility,
    _get_contract_market_price,
    _get_dte,
    _get_greek,
    _normalize_contract_type,
)
from agent_helpers._selector_normalize import _coerce_float, _normalize_option_id
from agent_helpers._selector_volatility import _assess_contract_volatility


def _contract_debug_snapshot(
    contract: dict[str, Any],
    *,
    reference_stock_price: float | None,
    market_context: dict[str, Any] | None = None,
    target_otm_distance: float | None = None,
    target_dte_bucket: str = "",
) -> dict[str, Any]:
    strike_price = _coerce_float(contract.get("strike_price"))
    return {
        "option_id": _normalize_option_id(contract.get("option_id")),
        "symbol": str(contract.get("symbol") or "").strip(),
        "contract_type": _normalize_contract_type(contract.get("contract_type")),
        "expiration_date": str(contract.get("expiration_date") or "").strip(),
        "strike_price": strike_price,
        "reference_stock_price": reference_stock_price,
        "target_dte_bucket": target_dte_bucket,
        "target_otm_distance": target_otm_distance,
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
        "implied_volatility": _get_contract_implied_volatility(contract),
        "delta": _get_greek(contract, "delta"),
        "gamma": _get_greek(contract, "gamma"),
        "theta": _get_greek(contract, "theta"),
        "vega": _get_greek(contract, "vega"),
        "theta_to_price": _get_theta_to_price(contract),
        "volatility_assessment": _assess_contract_volatility(
            contract,
            market_context=market_context or {},
            target_dte_bucket=target_dte_bucket,
        ),
    }


def _debug_contract_with_reasons(
    contract: dict[str, Any],
    *,
    reference_stock_price: float | None,
    market_context: dict[str, Any] | None = None,
    target_otm_distance: float | None = None,
    target_dte_bucket: str = "",
    rejection_reasons: list[str],
) -> dict[str, Any]:
    return {
        **_contract_debug_snapshot(
            contract,
            reference_stock_price=reference_stock_price,
            market_context=market_context,
            target_otm_distance=target_otm_distance,
            target_dte_bucket=target_dte_bucket,
        ),
        "rejection_reasons": rejection_reasons,
    }


def _build_simple_rejection_reasons(
    contract: dict[str, Any],
    *,
    normalized_decision: str,
    reference_stock_price: float | None,
    target_otm_distance: float,
    normalized_target_dte_bucket: str,
) -> list[str]:
    reasons: list[str] = []
    strike_price = _coerce_float(contract.get("strike_price"))
    if strike_price is None:
        reasons.append("missing_strike_price")
        return reasons

    if SIMPLE_REQUIRE_ONE_DOLLAR_OTM:
        if not _is_one_dollar_otm_contract(
            normalized_decision,
            strike_price,
            reference_stock_price,
            target_otm_distance=target_otm_distance,
        ):
            reasons.append("not_one_dollar_otm")
    elif not _is_otm_contract(normalized_decision, strike_price, reference_stock_price):
        reasons.append("not_otm")

    if normalized_target_dte_bucket in DTE_BUCKET_TO_TARGET_OTM_PCT:
        if not _meets_min_otm_distance(
            normalized_decision,
            strike_price,
            reference_stock_price,
            min_distance=target_otm_distance,
        ):
            reasons.append("otm_distance_below_preferred_min")
    elif not _is_otm_contract(normalized_decision, strike_price, reference_stock_price):
        reasons.append("not_otm")

    return reasons
