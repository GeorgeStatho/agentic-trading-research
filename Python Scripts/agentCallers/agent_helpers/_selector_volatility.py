from __future__ import annotations

from typing import Any

from agent_helpers._selector_config import (
    NEAR_TERM_EVENT_PRICING_DTE,
    SHORT_TERM_EVENT_PRICING_DTE,
)
from agent_helpers._selector_market import (
    _get_contract_implied_volatility,
    _get_contract_iv_percentile,
    _get_dte,
    _get_option_market_volatility_summary,
    _get_underlying_historical_volatility,
)
from agent_helpers.volatility import summarize_iv_vs_hv
from services.option_dte_buckets import normalize_target_dte_bucket


def _score_iv_percentile(iv_percentile: float | None) -> dict[str, Any]:
    if iv_percentile is None:
        return {
            "available": False,
            "iv_percentile": None,
            "band": "unknown",
            "penalty_points": 0,
            "guidance": "",
        }
    if iv_percentile <= 30.0:
        return {
            "available": True,
            "iv_percentile": iv_percentile,
            "band": "good_for_buying",
            "penalty_points": 0,
            "guidance": "IV percentile is low, which is favorable for buying options.",
        }
    if iv_percentile <= 60.0:
        return {
            "available": True,
            "iv_percentile": iv_percentile,
            "band": "normal",
            "penalty_points": 0,
            "guidance": "IV percentile is normal, so trade selection can proceed without a volatility penalty.",
        }
    if iv_percentile <= 80.0:
        return {
            "available": True,
            "iv_percentile": iv_percentile,
            "band": "elevated_requires_strong_confidence",
            "penalty_points": 1,
            "guidance": "IV percentile is elevated, so confidence should be reduced unless the setup is very strong.",
        }
    return {
        "available": True,
        "iv_percentile": iv_percentile,
        "band": "very_high_avoid_buying",
        "penalty_points": 2,
        "guidance": "IV percentile is very high, which usually argues against buying premium.",
    }


def _score_iv_hv_ratio(contract: dict[str, Any], market_context: dict[str, Any]) -> dict[str, Any]:
    contract_iv = _get_contract_implied_volatility(contract)
    historical_volatility = _get_underlying_historical_volatility(market_context)
    iv_vs_hv = summarize_iv_vs_hv(contract_iv, historical_volatility)
    if not iv_vs_hv.get("available"):
        market_level = _get_option_market_volatility_summary(market_context).get("atm_iv_vs_hv", {})
        if isinstance(market_level, dict) and market_level.get("available"):
            iv_vs_hv = dict(market_level)
        else:
            return {
                "available": False,
                "pricing_band": "unknown",
                "penalty_points": 0,
                "guidance": "",
            }

    pricing_band = str(iv_vs_hv.get("pricing_band") or "unknown")
    penalty_points = 1 if pricing_band == "expensive" else 2 if pricing_band == "very_expensive" else 0
    return {**iv_vs_hv, "penalty_points": penalty_points}


def _score_term_structure_warning(
    contract: dict[str, Any],
    market_context: dict[str, Any],
    *,
    target_dte_bucket: str,
) -> dict[str, Any]:
    term_structure = _get_option_market_volatility_summary(market_context).get("term_structure", {})
    if not isinstance(term_structure, dict) or not term_structure.get("event_risk_flag"):
        return {
            "available": bool(isinstance(term_structure, dict) and term_structure),
            "event_risk_flag": False,
            "band": "normal",
            "penalty_points": 0,
            "guidance": "",
        }

    contract_dte = _get_dte(contract)
    if contract_dte is None:
        normalized_bucket = normalize_target_dte_bucket(target_dte_bucket) or ""
        if normalized_bucket == "1_7":
            contract_dte = SHORT_TERM_EVENT_PRICING_DTE
        elif normalized_bucket == "7_14":
            contract_dte = NEAR_TERM_EVENT_PRICING_DTE

    if contract_dte is not None and contract_dte <= SHORT_TERM_EVENT_PRICING_DTE:
        return {
            "available": True,
            "event_risk_flag": True,
            "band": "avoid_very_short_dte",
            "penalty_points": 2,
            "guidance": "Short-term IV is much higher than longer-term IV, so very short DTE premium should be avoided.",
            "term_structure": term_structure,
        }
    if contract_dte is not None and contract_dte <= NEAR_TERM_EVENT_PRICING_DTE:
        return {
            "available": True,
            "event_risk_flag": True,
            "band": "caution_near_term_dte",
            "penalty_points": 1,
            "guidance": "Event-priced short-term IV is elevated, so near-term premium deserves caution.",
            "term_structure": term_structure,
        }
    return {
        "available": True,
        "event_risk_flag": True,
        "band": "prefer_longer_dte",
        "penalty_points": 0,
        "guidance": "The term structure warning favors moving out in time rather than buying front expiration premium.",
        "term_structure": term_structure,
    }


def _volatility_penalty_to_confidence_steps(total_penalty_points: int) -> int:
    if total_penalty_points >= 3:
        return 2
    if total_penalty_points >= 1:
        return 1
    return 0


def _assess_contract_volatility(
    contract: dict[str, Any],
    *,
    market_context: dict[str, Any],
    target_dte_bucket: str = "none",
) -> dict[str, Any]:
    iv_percentile = _score_iv_percentile(_get_contract_iv_percentile(contract, market_context))
    iv_hv = _score_iv_hv_ratio(contract, market_context)
    term_structure = _score_term_structure_warning(
        contract,
        market_context,
        target_dte_bucket=target_dte_bucket,
    )
    total_penalty_points = int(
        iv_percentile.get("penalty_points", 0)
        + iv_hv.get("penalty_points", 0)
        + term_structure.get("penalty_points", 0)
    )
    return {
        "implied_volatility": _get_contract_implied_volatility(contract),
        "historical_volatility": _get_underlying_historical_volatility(market_context),
        "iv_percentile": iv_percentile,
        "iv_hv": iv_hv,
        "term_structure": term_structure,
        "total_penalty_points": total_penalty_points,
        "confidence_penalty_steps": _volatility_penalty_to_confidence_steps(total_penalty_points),
    }
