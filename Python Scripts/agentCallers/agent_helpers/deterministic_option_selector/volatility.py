from __future__ import annotations

from typing import Any

from .config import (
    NEAR_TERM_EVENT_PRICING_DTE,
    SHORT_TERM_EVENT_PRICING_DTE,
)
from .market import (
    _get_contract_market_price,
    _get_contract_implied_volatility,
    _get_contract_iv_percentile,
    _get_dte,
    _get_greek,
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


def _score_vega_per_premium(contract: dict[str, Any], market_context: dict[str, Any]) -> dict[str, Any]:
    contract_price = _get_contract_market_price(contract)
    vega = _get_greek(contract, "vega")
    iv_percentile = _get_contract_iv_percentile(contract, market_context)
    if contract_price is None or contract_price <= 0 or vega is None or vega < 0:
        return {
            "available": False,
            "vega": vega,
            "contract_price": contract_price,
            "vega_per_premium": None,
            "band": "unknown",
            "selection_preference_score": 0.0,
            "confidence_penalty_points": 0,
            "guidance": "",
        }

    vega_per_premium = vega / contract_price
    if iv_percentile is None:
        return {
            "available": True,
            "vega": vega,
            "contract_price": contract_price,
            "vega_per_premium": round(vega_per_premium, 6),
            "band": "unclassified_without_iv_percentile",
            "selection_preference_score": 0.0,
            "confidence_penalty_points": 0,
            "guidance": "Vega is available, but no IV percentile was present to judge whether this sensitivity is attractive or expensive.",
        }

    if iv_percentile <= 30.0:
        if 0.02 <= vega_per_premium <= 0.05:
            return {
                "available": True,
                "vega": vega,
                "contract_price": contract_price,
                "vega_per_premium": round(vega_per_premium, 6),
                "band": "preferred_low_iv_vega",
                "selection_preference_score": -1.0,
                "confidence_penalty_points": 0,
                "guidance": "Low IV plus moderate-to-high vega per premium is attractive for long premium exposure.",
            }
        return {
            "available": True,
            "vega": vega,
            "contract_price": contract_price,
            "vega_per_premium": round(vega_per_premium, 6),
            "band": "acceptable_low_iv_vega",
            "selection_preference_score": 0.0,
            "confidence_penalty_points": 0,
            "guidance": "Low IV makes added vega sensitivity acceptable, but this contract is not in the preferred vega-per-premium pocket.",
        }

    if iv_percentile <= 60.0:
        if 0.01 <= vega_per_premium <= 0.03:
            return {
                "available": True,
                "vega": vega,
                "contract_price": contract_price,
                "vega_per_premium": round(vega_per_premium, 6),
                "band": "preferred_normal_iv_vega",
                "selection_preference_score": -0.5,
                "confidence_penalty_points": 0,
                "guidance": "Normal IV with moderate vega per premium is a solid fit.",
            }
        return {
            "available": True,
            "vega": vega,
            "contract_price": contract_price,
            "vega_per_premium": round(vega_per_premium, 6),
            "band": "acceptable_normal_iv_vega",
            "selection_preference_score": 0.0,
            "confidence_penalty_points": 0,
            "guidance": "Normal IV leaves this vega exposure acceptable, but not especially preferred.",
        }

    if iv_percentile > 70.0 and vega_per_premium > 0.05:
        return {
            "available": True,
            "vega": vega,
            "contract_price": contract_price,
            "vega_per_premium": round(vega_per_premium, 6),
            "band": "very_high_vega_in_high_iv",
            "selection_preference_score": 2.0,
            "confidence_penalty_points": 1,
            "guidance": "High IV plus very high vega per premium is unusually expensive and deserves strong caution.",
        }
    if iv_percentile > 70.0 and vega_per_premium > 0.03:
        return {
            "available": True,
            "vega": vega,
            "contract_price": contract_price,
            "vega_per_premium": round(vega_per_premium, 6),
            "band": "high_vega_in_high_iv",
            "selection_preference_score": 1.0,
            "confidence_penalty_points": 0,
            "guidance": "High IV plus elevated vega per premium is a reason to prefer a less vega-heavy contract when possible.",
        }
    return {
        "available": True,
        "vega": vega,
        "contract_price": contract_price,
        "vega_per_premium": round(vega_per_premium, 6),
        "band": "acceptable_elevated_iv_vega",
        "selection_preference_score": 0.0,
        "confidence_penalty_points": 0,
        "guidance": "This vega exposure is acceptable for the current IV regime.",
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
    vega_profile = _score_vega_per_premium(contract, market_context)
    term_structure = _score_term_structure_warning(
        contract,
        market_context,
        target_dte_bucket=target_dte_bucket,
    )
    core_penalty_points = int(
        iv_percentile.get("penalty_points", 0)
        + iv_hv.get("penalty_points", 0)
        + term_structure.get("penalty_points", 0)
    )
    vega_confidence_penalty_points = int(vega_profile.get("confidence_penalty_points", 0))
    total_penalty_points = core_penalty_points + vega_confidence_penalty_points
    return {
        "implied_volatility": _get_contract_implied_volatility(contract),
        "historical_volatility": _get_underlying_historical_volatility(market_context),
        "iv_percentile": iv_percentile,
        "iv_hv": iv_hv,
        "vega_profile": vega_profile,
        "term_structure": term_structure,
        "core_penalty_points": core_penalty_points,
        "vega_confidence_penalty_points": vega_confidence_penalty_points,
        "total_penalty_points": total_penalty_points,
        "selection_preference_score": float(vega_profile.get("selection_preference_score", 0.0)),
        "confidence_penalty_steps": _volatility_penalty_to_confidence_steps(total_penalty_points),
    }
