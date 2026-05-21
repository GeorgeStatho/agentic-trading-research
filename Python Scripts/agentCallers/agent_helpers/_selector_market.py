from __future__ import annotations

from datetime import date, datetime
from typing import Any

from agent_helpers._selector_config import MAX_TARGET_OTM_DOLLARS, MIN_TARGET_OTM_DOLLARS
from agent_helpers._selector_normalize import _coerce_float
from services.option_dte_buckets import get_bucket_target_otm_pct, normalize_target_dte_bucket


def _resolve_target_otm_distance(
    *,
    reference_stock_price: float | None,
    target_dte_bucket: str,
    default_distance: float,
) -> float:
    normalized_bucket = normalize_target_dte_bucket(target_dte_bucket)
    target_pct = get_bucket_target_otm_pct(normalized_bucket)
    if target_pct is None or reference_stock_price is None or reference_stock_price <= 0:
        return default_distance
    target_otm_dollars = reference_stock_price * target_pct
    return min(max(target_otm_dollars, MIN_TARGET_OTM_DOLLARS), MAX_TARGET_OTM_DOLLARS)


def _normalize_contract_type(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    contract_type = str(enum_value if enum_value not in (None, "") else value or "").strip().lower()
    if "." in contract_type:
        contract_type = contract_type.rsplit(".", 1)[-1]
    contract_type = {
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
    }.get(contract_type, contract_type)
    return contract_type if contract_type in {"call", "put"} else ""


def _get_reference_stock_price(market_context: dict[str, Any]) -> float | None:
    stock_snapshot = market_context.get("current_stock_price", {})
    for key in ("latest_trade_price", "price", "ask_price", "midpoint_price", "bid_price"):
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


def _normalize_percentile(value: Any) -> float | None:
    percentile = _coerce_float(value)
    if percentile is None:
        return None
    if 0.0 <= percentile <= 1.0:
        percentile *= 100.0
    if percentile < 0.0 or percentile > 100.0:
        return None
    return round(percentile, 6)


def _get_contract_implied_volatility(contract: dict[str, Any]) -> float | None:
    implied_volatility = _coerce_float(contract.get("implied_volatility"))
    if implied_volatility is None or implied_volatility < 0:
        return None
    return implied_volatility


def _get_option_market_volatility_summary(market_context: dict[str, Any]) -> dict[str, Any]:
    option_market = market_context.get("option_market", {})
    if not isinstance(option_market, dict):
        return {}
    volatility_summary = option_market.get("volatility_summary", {})
    return volatility_summary if isinstance(volatility_summary, dict) else {}


def _get_underlying_historical_volatility(market_context: dict[str, Any]) -> float | None:
    underlying_price_history = market_context.get("underlying_price_history", {})
    if isinstance(underlying_price_history, dict):
        for key in ("historical_volatility_20d", "historical_volatility_60d"):
            historical_volatility = _coerce_float(underlying_price_history.get(key))
            if historical_volatility is not None and historical_volatility > 0:
                return historical_volatility

    volatility_summary = _get_option_market_volatility_summary(market_context)
    for key in ("historical_volatility_20d", "historical_volatility_60d"):
        historical_volatility = _coerce_float(volatility_summary.get(key))
        if historical_volatility is not None and historical_volatility > 0:
            return historical_volatility
    return None


def _get_contract_iv_percentile(contract: dict[str, Any], market_context: dict[str, Any]) -> float | None:
    for key in (
        "iv_percentile",
        "dte_bucket_iv_percentile",
        "tenor_iv_percentile",
        "implied_volatility_percentile",
        "implied_vol_percentile",
        "ivPercentile",
    ):
        percentile = _normalize_percentile(contract.get(key))
        if percentile is not None:
            return percentile

    volatility_summary = _get_option_market_volatility_summary(market_context)
    bucket_key = str(contract.get("dte_bucket") or "").strip()
    bucket_percentiles = volatility_summary.get("dte_bucket_iv_percentiles", {})
    if bucket_key and isinstance(bucket_percentiles, dict):
        percentile = _normalize_percentile(bucket_percentiles.get(bucket_key))
        if percentile is not None:
            return percentile

    term_structure = volatility_summary.get("term_structure", {})
    if isinstance(term_structure, dict):
        days_to_expiration = _get_dte(contract)
        short_term_percentile = _normalize_percentile(term_structure.get("short_term_iv_percentile"))
        longer_term_percentile = _normalize_percentile(term_structure.get("longer_term_iv_percentile"))
        if days_to_expiration is not None:
            if days_to_expiration <= 14 and short_term_percentile is not None:
                return short_term_percentile
            if days_to_expiration >= 15 and longer_term_percentile is not None:
                return longer_term_percentile
    return None
