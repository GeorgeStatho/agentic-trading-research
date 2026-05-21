from __future__ import annotations

"""Shared volatility helpers for selector and agent-stage market context.

These helpers intentionally avoid external market-data dependencies so they can
be reused from unit tests, option-chain serialization, and prompt-building
code without pulling in Alpaca or yfinance at import time.
"""

import math
import statistics
from collections import defaultdict
from datetime import date, datetime
from typing import Any


TRADING_DAYS_PER_YEAR = 252

__all__ = [
    "TRADING_DAYS_PER_YEAR",
    "compute_realized_volatility",
    "compute_percentile_rank",
    "summarize_iv_vs_hv",
    "summarize_iv_term_structure",
    "summarize_option_iv",
]


def _coerce_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_contract_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    replacements = {
        "c": "call",
        "p": "put",
        "calls": "call",
        "puts": "put",
    }
    normalized = replacements.get(normalized, normalized)
    return normalized if normalized in {"call", "put"} else ""


def _extract_implied_volatility(contract: dict[str, Any]) -> float | None:
    implied_volatility = _coerce_float(contract.get("implied_volatility"))
    if implied_volatility is None or implied_volatility < 0:
        return None
    return implied_volatility


def _extract_strike_price(contract: dict[str, Any]) -> float | None:
    strike_price = _coerce_float(contract.get("strike_price"))
    if strike_price is None or strike_price <= 0:
        return None
    return strike_price


def _extract_expiration_date(contract: dict[str, Any]) -> date | None:
    expiration_text = str(contract.get("expiration_date") or "").strip()
    if not expiration_text:
        return None
    try:
        return datetime.strptime(expiration_text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _extract_dte(contract: dict[str, Any]) -> int | None:
    for field_name in ("dte", "days_to_expiration", "daysTillExpiration"):
        explicit_dte = _coerce_float(contract.get(field_name))
        if explicit_dte is not None:
            return int(round(explicit_dte))
    expiration_date = _extract_expiration_date(contract)
    if expiration_date is None:
        return None
    return (expiration_date - date.today()).days


def _contract_atm_sort_key(
    contract: dict[str, Any],
    *,
    reference_stock_price: float,
) -> tuple[float, str, str]:
    strike_price = _extract_strike_price(contract)
    strike_distance = (
        abs(strike_price - reference_stock_price)
        if strike_price is not None
        else float("inf")
    )
    expiration_date = str(contract.get("expiration_date") or "9999-12-31")
    symbol = str(contract.get("symbol") or "")
    return strike_distance, expiration_date, symbol


def _pick_atm_contracts_for_side(
    contracts: list[dict[str, Any]],
    *,
    contract_type: str,
    reference_stock_price: float | None,
) -> list[dict[str, Any]]:
    filtered_contracts = [
        contract
        for contract in contracts
        if _normalize_contract_type(contract.get("contract_type")) == contract_type
        and _extract_implied_volatility(contract) is not None
    ]
    if not filtered_contracts:
        return []
    if reference_stock_price is None:
        return list(filtered_contracts)
    return sorted(
        filtered_contracts,
        key=lambda contract: _contract_atm_sort_key(
            contract,
            reference_stock_price=reference_stock_price,
        ),
    )


def _compute_atm_mean_iv_for_contracts(
    contracts: list[dict[str, Any]],
    *,
    reference_stock_price: float | None,
) -> tuple[float | None, dict[str, Any]]:
    call_candidates = _pick_atm_contracts_for_side(
        contracts,
        contract_type="call",
        reference_stock_price=reference_stock_price,
    )
    put_candidates = _pick_atm_contracts_for_side(
        contracts,
        contract_type="put",
        reference_stock_price=reference_stock_price,
    )
    selected_contracts = []
    if call_candidates:
        selected_contracts.append(call_candidates[0])
    if put_candidates:
        selected_contracts.append(put_candidates[0])
    selected_ivs = [
        contract["implied_volatility"]
        for contract in selected_contracts
        if _extract_implied_volatility(contract) is not None
    ]
    if not selected_ivs:
        return None, {
            "call_symbol": "",
            "put_symbol": "",
            "call_iv": None,
            "put_iv": None,
            "call_dte": None,
            "put_dte": None,
        }
    return round(statistics.mean(selected_ivs), 6), {
        "call_symbol": str(call_candidates[0].get("symbol") or "") if call_candidates else "",
        "put_symbol": str(put_candidates[0].get("symbol") or "") if put_candidates else "",
        "call_iv": _extract_implied_volatility(call_candidates[0]) if call_candidates else None,
        "put_iv": _extract_implied_volatility(put_candidates[0]) if put_candidates else None,
        "call_dte": _extract_dte(call_candidates[0]) if call_candidates else None,
        "put_dte": _extract_dte(put_candidates[0]) if put_candidates else None,
    }


def _group_contracts_by_expiration(
    contracts: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped_contracts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for contract in contracts:
        expiration_date = str(contract.get("expiration_date") or "").strip()
        if expiration_date:
            grouped_contracts[expiration_date].append(contract)
    return dict(grouped_contracts)


def _pick_term_snapshot(
    contracts: list[dict[str, Any]],
    *,
    reference_stock_price: float | None,
    target_dte: int,
) -> tuple[float | None, dict[str, Any]]:
    grouped_contracts = _group_contracts_by_expiration(contracts)
    best_snapshot: dict[str, Any] | None = None

    for expiration_date, expiration_contracts in grouped_contracts.items():
        atm_mean_iv, details = _compute_atm_mean_iv_for_contracts(
            expiration_contracts,
            reference_stock_price=reference_stock_price,
        )
        representative_dte = next(
            (
                dte
                for dte in (
                    details.get("call_dte"),
                    details.get("put_dte"),
                )
                if dte is not None
            ),
            None,
        )
        if atm_mean_iv is None or representative_dte is None:
            continue

        candidate_snapshot = {
            "expiration_date": expiration_date,
            "dte": representative_dte,
            "atm_iv": atm_mean_iv,
            "contract_count": len(expiration_contracts),
            "details": details,
        }
        if best_snapshot is None:
            best_snapshot = candidate_snapshot
            continue

        candidate_sort_key = (
            abs(representative_dte - int(target_dte)),
            abs(atm_mean_iv),
            representative_dte,
            expiration_date,
        )
        best_sort_key = (
            abs(int(best_snapshot["dte"]) - int(target_dte)),
            abs(float(best_snapshot["atm_iv"])),
            int(best_snapshot["dte"]),
            str(best_snapshot["expiration_date"]),
        )
        if candidate_sort_key < best_sort_key:
            best_snapshot = candidate_snapshot

    if best_snapshot is None:
        return None, {
            "expiration_date": "",
            "dte": None,
            "atm_iv": None,
            "contract_count": 0,
            "details": {
                "call_symbol": "",
                "put_symbol": "",
                "call_iv": None,
                "put_iv": None,
                "call_dte": None,
                "put_dte": None,
            },
        }
    return float(best_snapshot["atm_iv"]), best_snapshot


def compute_realized_volatility(
    closes: list[Any] | tuple[Any, ...],
    window_days: int,
    *,
    trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float | None:
    """Return annualized realized volatility from the most recent closing prices.

    The return value is a decimal ratio, not a percentage. For example, 0.25
    represents 25% annualized realized volatility.
    """

    normalized_window_days = max(2, int(window_days))
    valid_closes = [
        close
        for close in (_coerce_float(value) for value in closes)
        if close is not None and close > 0
    ]
    if len(valid_closes) < normalized_window_days:
        return None

    window = valid_closes[-normalized_window_days:]
    log_returns = [
        math.log(current_close / previous_close)
        for previous_close, current_close in zip(window, window[1:])
        if previous_close > 0 and current_close > 0
    ]
    if len(log_returns) < 2:
        return None

    daily_std_dev = statistics.stdev(log_returns)
    annualized_volatility = daily_std_dev * math.sqrt(max(1, int(trading_days_per_year)))
    return round(annualized_volatility, 6)


def compute_percentile_rank(
    current_value: Any,
    historical_values: list[Any] | tuple[Any, ...],
) -> float | None:
    """Return the percentile rank of ``current_value`` within prior observations.

    The return value is a 0-100 percentile. Historical values are expected to
    be decimal ratios rather than percentages.
    """

    normalized_current_value = _coerce_float(current_value)
    if normalized_current_value is None:
        return None

    normalized_history = [
        value
        for value in (_coerce_float(raw_value) for raw_value in historical_values)
        if value is not None
    ]
    if not normalized_history:
        return None

    less_or_equal_count = sum(
        1
        for historical_value in normalized_history
        if historical_value <= normalized_current_value
    )
    percentile_rank = (less_or_equal_count / len(normalized_history)) * 100.0
    return round(percentile_rank, 6)


def summarize_iv_vs_hv(
    implied_volatility: Any,
    historical_volatility: Any,
) -> dict[str, Any]:
    """Compare implied volatility against realized/historical volatility.

    All volatility values are decimal ratios rather than percentages. The
    returned ratio is ``implied_volatility / historical_volatility``.
    """

    normalized_iv = _coerce_float(implied_volatility)
    normalized_hv = _coerce_float(historical_volatility)
    if normalized_iv is None or normalized_iv < 0 or normalized_hv is None or normalized_hv <= 0:
        return {
            "available": False,
            "implied_volatility": normalized_iv,
            "historical_volatility": normalized_hv,
            "iv_hv_ratio": None,
            "pricing_band": "unknown",
            "label": "",
            "guidance": "",
        }

    iv_hv_ratio = round(normalized_iv / normalized_hv, 6)
    if iv_hv_ratio < 1.1:
        pricing_band = "reasonably_priced"
        label = "Options are reasonably priced"
        guidance = "Current IV is close to historical volatility."
    elif iv_hv_ratio < 1.5:
        pricing_band = "acceptable"
        label = "Acceptable"
        guidance = "IV is elevated versus historical volatility but still within an acceptable range."
    elif iv_hv_ratio <= 2.0:
        pricing_band = "expensive"
        label = "Expensive, reduce confidence"
        guidance = "Premium looks expensive versus historical volatility, so long-option entries deserve lower confidence."
    else:
        pricing_band = "very_expensive"
        label = "Very expensive, usually avoid buying"
        guidance = "IV is far above historical volatility, which usually argues against buying premium."

    return {
        "available": True,
        "implied_volatility": round(normalized_iv, 6),
        "historical_volatility": round(normalized_hv, 6),
        "iv_hv_ratio": iv_hv_ratio,
        "pricing_band": pricing_band,
        "label": label,
        "guidance": guidance,
    }


def summarize_iv_term_structure(
    contracts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    reference_stock_price: float | None,
    *,
    short_term_target_dte: int = 7,
    short_term_max_dte: int = 10,
    longer_term_target_dte: int = 30,
    longer_term_min_dte: int = 20,
    longer_term_max_dte: int = 45,
    event_warning_ratio: float = 1.35,
    event_warning_spread: float = 0.15,
) -> dict[str, Any]:
    """Compare front-month ATM IV against longer-dated ATM IV.

    A much higher short-dated IV can indicate event pricing or a near-term
    catalyst. All volatility values are decimal ratios rather than percentages.
    """

    normalized_reference_stock_price = (
        float(reference_stock_price)
        if reference_stock_price is not None and reference_stock_price > 0
        else None
    )
    short_term_contracts = []
    longer_term_contracts = []
    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        dte = _extract_dte(contract)
        if dte is None or dte < 0:
            continue
        if dte <= int(short_term_max_dte):
            short_term_contracts.append(contract)
        elif int(longer_term_min_dte) <= dte <= int(longer_term_max_dte):
            longer_term_contracts.append(contract)

    short_term_atm_iv, short_term_snapshot = _pick_term_snapshot(
        short_term_contracts,
        reference_stock_price=normalized_reference_stock_price,
        target_dte=int(short_term_target_dte),
    )
    longer_term_atm_iv, longer_term_snapshot = _pick_term_snapshot(
        longer_term_contracts,
        reference_stock_price=normalized_reference_stock_price,
        target_dte=int(longer_term_target_dte),
    )
    short_term_details = short_term_snapshot["details"]
    longer_term_details = longer_term_snapshot["details"]

    iv_ratio = (
        round(short_term_atm_iv / longer_term_atm_iv, 6)
        if short_term_atm_iv is not None
        and longer_term_atm_iv is not None
        and longer_term_atm_iv > 0
        else None
    )
    iv_spread = (
        round(short_term_atm_iv - longer_term_atm_iv, 6)
        if short_term_atm_iv is not None and longer_term_atm_iv is not None
        else None
    )
    event_risk_flag = bool(
        iv_ratio is not None
        and iv_spread is not None
        and iv_ratio >= float(event_warning_ratio)
        and iv_spread >= float(event_warning_spread)
        and short_term_atm_iv > longer_term_atm_iv
    )
    warning_message = ""
    if event_risk_flag:
        warning_message = (
            f"Short-dated ATM IV ({round(short_term_atm_iv * 100, 2)}%) is materially "
            f"above longer-dated ATM IV ({round(longer_term_atm_iv * 100, 2)}%), "
            "which can signal event pricing or a near-term catalyst."
        )

    return {
        "available": short_term_atm_iv is not None or longer_term_atm_iv is not None,
        "comparison_available": (
            short_term_atm_iv is not None and longer_term_atm_iv is not None
        ),
        "reference_stock_price": normalized_reference_stock_price,
        "short_term_target_dte": int(short_term_target_dte),
        "short_term_max_dte": int(short_term_max_dte),
        "longer_term_target_dte": int(longer_term_target_dte),
        "longer_term_min_dte": int(longer_term_min_dte),
        "longer_term_max_dte": int(longer_term_max_dte),
        "short_term_contract_count": len(short_term_contracts),
        "longer_term_contract_count": len(longer_term_contracts),
        "short_term_expiration_date": str(short_term_snapshot["expiration_date"] or ""),
        "short_term_selected_dte": short_term_snapshot["dte"],
        "short_term_atm_iv": short_term_atm_iv,
        "longer_term_expiration_date": str(longer_term_snapshot["expiration_date"] or ""),
        "longer_term_selected_dte": longer_term_snapshot["dte"],
        "longer_term_atm_iv": longer_term_atm_iv,
        "iv_ratio": iv_ratio,
        "iv_spread": iv_spread,
        "event_risk_flag": event_risk_flag,
        "warning_message": warning_message,
        "curve_shape": (
            "inverted"
            if iv_spread is not None and iv_spread > 0
            else "flat_or_contango"
            if iv_spread is not None
            else "unknown"
        ),
        "short_term_details": short_term_details,
        "longer_term_details": longer_term_details,
    }


def summarize_option_iv(
    contracts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    reference_stock_price: float | None,
    *,
    historical_volatility: float | None = None,
) -> dict[str, Any]:
    """Summarize current chain IV with nearest-ATM call/put markers.

    All volatility values are returned as decimal ratios rather than
    percentages.
    """

    normalized_reference_stock_price = (
        float(reference_stock_price)
        if reference_stock_price is not None and reference_stock_price > 0
        else None
    )

    contracts_with_iv: list[dict[str, Any]] = []
    call_contracts_with_iv: list[dict[str, Any]] = []
    put_contracts_with_iv: list[dict[str, Any]] = []
    iv_values: list[float] = []

    for raw_contract in contracts:
        if not isinstance(raw_contract, dict):
            continue
        implied_volatility = _extract_implied_volatility(raw_contract)
        if implied_volatility is None:
            continue

        normalized_contract = {
            **raw_contract,
            "contract_type": _normalize_contract_type(raw_contract.get("contract_type")),
            "implied_volatility": implied_volatility,
        }
        contracts_with_iv.append(normalized_contract)
        iv_values.append(implied_volatility)

        if normalized_contract["contract_type"] == "call":
            call_contracts_with_iv.append(normalized_contract)
        elif normalized_contract["contract_type"] == "put":
            put_contracts_with_iv.append(normalized_contract)

    if not iv_values:
        return {
            "available": False,
            "reference_stock_price": normalized_reference_stock_price,
            "contract_count": len(contracts),
            "contracts_with_iv_count": 0,
            "call_contract_count": 0,
            "put_contract_count": 0,
            "mean_implied_volatility": None,
            "median_implied_volatility": None,
            "min_implied_volatility": None,
            "max_implied_volatility": None,
            "atm_call_iv": None,
            "atm_put_iv": None,
            "atm_mean_iv": None,
            "atm_call_symbol": "",
            "atm_put_symbol": "",
            "atm_call_strike_price": None,
            "atm_put_strike_price": None,
            "atm_iv_vs_hv": summarize_iv_vs_hv(None, historical_volatility),
        }

    atm_call_contract = None
    atm_put_contract = None
    if normalized_reference_stock_price is not None:
        if call_contracts_with_iv:
            atm_call_contract = min(
                call_contracts_with_iv,
                key=lambda contract: _contract_atm_sort_key(
                    contract,
                    reference_stock_price=normalized_reference_stock_price,
                ),
            )
        if put_contracts_with_iv:
            atm_put_contract = min(
                put_contracts_with_iv,
                key=lambda contract: _contract_atm_sort_key(
                    contract,
                    reference_stock_price=normalized_reference_stock_price,
                ),
            )

    atm_values = [
        contract["implied_volatility"]
        for contract in (atm_call_contract, atm_put_contract)
        if contract is not None
    ]
    atm_mean_iv = round(statistics.mean(atm_values), 6) if atm_values else None
    term_structure = summarize_iv_term_structure(
        contracts,
        normalized_reference_stock_price,
    )
    atm_iv_vs_hv = summarize_iv_vs_hv(atm_mean_iv, historical_volatility)

    return {
        "available": True,
        "reference_stock_price": normalized_reference_stock_price,
        "contract_count": len(contracts),
        "contracts_with_iv_count": len(contracts_with_iv),
        "call_contract_count": len(call_contracts_with_iv),
        "put_contract_count": len(put_contracts_with_iv),
        "mean_implied_volatility": round(statistics.mean(iv_values), 6),
        "median_implied_volatility": round(statistics.median(iv_values), 6),
        "min_implied_volatility": round(min(iv_values), 6),
        "max_implied_volatility": round(max(iv_values), 6),
        "atm_call_iv": (
            round(float(atm_call_contract["implied_volatility"]), 6)
            if atm_call_contract is not None
            else None
        ),
        "atm_put_iv": (
            round(float(atm_put_contract["implied_volatility"]), 6)
            if atm_put_contract is not None
            else None
        ),
        "atm_mean_iv": atm_mean_iv,
        "atm_call_symbol": str(atm_call_contract.get("symbol") or "") if atm_call_contract else "",
        "atm_put_symbol": str(atm_put_contract.get("symbol") or "") if atm_put_contract else "",
        "atm_call_strike_price": _extract_strike_price(atm_call_contract or {}),
        "atm_put_strike_price": _extract_strike_price(atm_put_contract or {}),
        "atm_iv_vs_hv": atm_iv_vs_hv,
        "term_structure": term_structure,
    }
