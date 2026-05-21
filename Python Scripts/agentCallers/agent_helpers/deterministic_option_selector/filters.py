from __future__ import annotations

from typing import Any

from .normalize import _coerce_float, _normalize_option_id
from .market import (
    _get_ask_price,
    _get_bid_price,
    _get_contract_market_price,
    _get_dte,
    _get_greek,
)
from .volatility import _assess_contract_volatility
from services.option_dte_buckets import get_dte_bucket_range


def _resolve_dte_range(
    *,
    target_dte_bucket: str,
    default_min_dte: int,
    default_max_dte: int,
) -> tuple[int, int]:
    bucket_range = get_dte_bucket_range(target_dte_bucket)
    return bucket_range if bucket_range is not None else (default_min_dte, default_max_dte)


def _contract_matches_target_dte_bucket(contract: dict[str, Any], target_dte_bucket: str) -> bool:
    bucket_range = get_dte_bucket_range(target_dte_bucket)
    if bucket_range is None:
        return True
    dte = _get_dte(contract)
    if dte is None:
        return False
    min_dte, max_dte = bucket_range
    return min_dte <= dte <= max_dte


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


def _is_otm_contract(contract_type: str, strike_price: float | None, reference_stock_price: float | None) -> bool:
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
    *,
    target_otm_distance: float,
) -> bool:
    if strike_price is None or reference_stock_price is None:
        return False
    if contract_type == "call":
        return strike_price >= (reference_stock_price + target_otm_distance)
    if contract_type == "put":
        return strike_price <= (reference_stock_price - target_otm_distance)
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


def _otm_distance(contract_type: str, strike_price: float | None, reference_stock_price: float | None) -> float:
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
    return abs(_otm_distance(contract_type, strike_price, reference_stock_price) - target_distance)


def _basic_sort_key(
    contract: dict[str, Any],
    *,
    normalized_decision: str,
    reference_stock_price: float | None,
    market_context: dict[str, Any] | None = None,
    prefer_target_otm: bool,
    target_distance: float,
    target_abs_delta: float,
    target_dte_bucket: str = "none",
) -> tuple[float, float, float, float, str, float, int]:
    contract_price = _get_contract_market_price(contract)
    volatility_assessment = _assess_contract_volatility(
        contract,
        market_context=market_context or {},
        target_dte_bucket=target_dte_bucket,
    )
    strike_price = _coerce_float(contract.get("strike_price"))
    distance = (
        _distance_to_target_otm(
            normalized_decision,
            strike_price,
            reference_stock_price,
            target_distance=target_distance,
        )
        if prefer_target_otm
        else _otm_distance(normalized_decision, strike_price, reference_stock_price)
    )
    delta = _get_greek(contract, "delta")
    open_interest = _coerce_float(contract.get("open_interest"))
    return (
        0.0 if contract_price is not None else 1.0,
        float(volatility_assessment["total_penalty_points"]),
        distance,
        abs(abs(delta) - target_abs_delta) if delta is not None else 999.0,
        str(contract.get("expiration_date") or "9999-12-31"),
        -(open_interest if open_interest is not None else -1.0),
        _normalize_option_id(contract.get("option_id")) or 10**9,
    )
