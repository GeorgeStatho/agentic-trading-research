from __future__ import annotations

from typing import Any

from services.option_momentum import MOMENTUM_BAD, MOMENTUM_GOOD, MOMENTUM_UNKNOWN

from ._option_positions_defaults import OptionPriceMomentumConfig
from .utils import safe_float

MOMENTUM_MIXED = "mixed"


def _momentum_negative_score(status: str) -> float:
    normalized_status = str(status or "").strip().lower()
    if normalized_status == MOMENTUM_BAD:
        return 1.0
    if normalized_status == MOMENTUM_MIXED:
        return 0.5
    return 0.0


def evaluate_option_price_momentum(
    *,
    entry_option_price: Any,
    current_option_mid_price: Any,
    unrealized_pl_ratio: Any,
    max_pnl_pct: Any,
    config: OptionPriceMomentumConfig,
) -> dict[str, Any]:
    entry_price = safe_float(entry_option_price)
    current_mid_price = safe_float(current_option_mid_price)
    current_pnl_ratio = safe_float(unrealized_pl_ratio)
    peak_pnl_ratio = safe_float(max_pnl_pct)

    reasons: list[str] = []
    if entry_price is None or entry_price <= 0:
        reasons.append("Entry option price was missing or invalid.")
    if current_mid_price is None or current_mid_price <= 0:
        reasons.append("Current option mid price was missing or invalid.")
    if current_pnl_ratio is None and (entry_price is None or current_mid_price is None):
        reasons.append("Option momentum could not be resolved without current option pricing or P/L.")
    if reasons:
        return {
            "status": MOMENTUM_UNKNOWN,
            "reasons": reasons,
            "entry_option_price": entry_price,
            "current_option_mid_price": current_mid_price,
            "current_pnl_ratio": current_pnl_ratio,
            "peak_pnl_ratio": peak_pnl_ratio,
            "giveback_from_peak": None,
        }

    current_return_ratio = None
    if entry_price is not None and entry_price > 0 and current_mid_price is not None:
        current_return_ratio = round((current_mid_price - entry_price) / entry_price, 6)
    primary_ratio = current_pnl_ratio if current_pnl_ratio is not None else current_return_ratio
    giveback_from_peak = None
    if primary_ratio is not None and peak_pnl_ratio is not None:
        giveback_from_peak = round(max(0.0, peak_pnl_ratio - primary_ratio), 6)

    if primary_ratio is None:
        status = MOMENTUM_UNKNOWN
        reasons.append("Option momentum could not resolve a usable return ratio.")
    elif giveback_from_peak is not None and giveback_from_peak >= config.bad_giveback_threshold:
        status = MOMENTUM_BAD
        reasons.append("Option premium gave back materially from its tracked peak.")
    elif primary_ratio < 0:
        status = MOMENTUM_BAD
        reasons.append("Option premium moved below its average entry price.")
    elif giveback_from_peak is not None and giveback_from_peak >= config.mixed_giveback_threshold:
        status = MOMENTUM_MIXED
        reasons.append("Option premium stayed profitable but started to give back a noticeable share of gains.")
    elif primary_ratio >= config.good_profit_threshold:
        status = MOMENTUM_GOOD
        reasons.append("Option premium stayed comfortably above entry and near the recent peak.")
    else:
        status = MOMENTUM_MIXED
        reasons.append("Option premium remained near entry without a strong continuation signal.")

    return {
        "status": status,
        "reasons": reasons,
        "entry_option_price": entry_price,
        "current_option_mid_price": current_mid_price,
        "current_pnl_ratio": primary_ratio,
        "peak_pnl_ratio": peak_pnl_ratio,
        "giveback_from_peak": giveback_from_peak,
    }


def evaluate_combined_option_momentum(
    *,
    option_momentum: dict[str, Any],
    underlying_momentum: dict[str, Any],
) -> dict[str, Any]:
    option_status = str(option_momentum.get("status") or "").strip().lower()
    underlying_status = str(underlying_momentum.get("status") or "").strip().lower()
    reasons = list(option_momentum.get("reasons") or [])

    if underlying_status != MOMENTUM_UNKNOWN:
        reasons.extend(list(underlying_momentum.get("reasons") or []))

    if option_status == MOMENTUM_UNKNOWN:
        combined_status = MOMENTUM_UNKNOWN
        reasons.append("Option price momentum was unavailable, so the combined signal stayed informational only.")
    elif option_status == MOMENTUM_BAD:
        combined_status = MOMENTUM_MIXED if underlying_status == MOMENTUM_GOOD else MOMENTUM_BAD
    elif option_status == MOMENTUM_GOOD:
        combined_status = MOMENTUM_MIXED if underlying_status == MOMENTUM_BAD else MOMENTUM_GOOD
    else:
        combined_status = MOMENTUM_BAD if underlying_status == MOMENTUM_BAD else MOMENTUM_MIXED

    return {
        "status": combined_status,
        "negative_score": _momentum_negative_score(combined_status),
        "option_status": option_status,
        "underlying_status": underlying_status,
        "reasons": reasons,
    }
