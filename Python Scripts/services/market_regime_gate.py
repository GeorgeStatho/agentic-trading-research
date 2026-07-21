from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MarketRegimePolicy:
    policy_version: str = "market-regime-v1"
    mode: str = "enforce"

    @classmethod
    def from_env(cls) -> "MarketRegimePolicy":
        mode = str(os.getenv("MARKET_REGIME_GATE_MODE", "enforce")).strip().lower()
        if mode not in {"observe", "shadow", "enforce"}:
            mode = "enforce"
        return cls(mode=mode)


def evaluate_market_regime_gate(
    *,
    proposed_decision: str,
    manager_confidence: str,
    regime: dict[str, Any],
    policy: MarketRegimePolicy | None = None,
) -> dict[str, Any]:
    """Map a deterministic regime state to an auditable execution permission."""
    active_policy = policy or MarketRegimePolicy.from_env()
    decision = str(proposed_decision or "").strip().lower()
    confidence = str(manager_confidence or "").strip().lower()
    state = str((regime or {}).get("state") or "unknown").strip().lower()
    macro_window = (regime or {}).get("macro_event_window")
    macro_active = isinstance(macro_window, dict) and macro_window.get("active") is True
    reasons: list[str] = []
    would_allow = True

    if decision not in {"call", "put"}:
        would_allow = False
        reasons.append("regime_gate_requires_directional_decision")
    elif macro_active:
        would_allow = False
        reasons.append("regime_macro_blackout_active")
    elif state in {"unknown", "stressed", "range"}:
        would_allow = False
        reasons.append(f"regime_{state}_blocks_new_directional_entries")
    elif state == "high_volatility":
        if confidence != "high":
            would_allow = False
            reasons.extend(["regime_high_volatility_requires_high_confidence", f"manager_confidence_{confidence or 'unknown'}"])
        else:
            reasons.append("regime_high_volatility_high_confidence_only")
    elif state == "trend_up":
        if decision == "put":
            would_allow = False
            reasons.append("regime_trend_up_blocks_countertrend_put")
        elif confidence != "high":
            would_allow = False
            reasons.extend(["regime_trend_up_requires_high_confidence_call", f"manager_confidence_{confidence or 'unknown'}"])
        else:
            reasons.append("regime_trend_up_aligned_call")
    elif state == "trend_down":
        if decision == "call":
            would_allow = False
            reasons.append("regime_trend_down_blocks_countertrend_call")
        elif confidence != "high":
            would_allow = False
            reasons.extend(["regime_trend_down_requires_high_confidence_put", f"manager_confidence_{confidence or 'unknown'}"])
        else:
            reasons.append("regime_trend_down_aligned_put")
    else:
        would_allow = False
        reasons.append("regime_state_unrecognized")

    allowed = would_allow or active_policy.mode in {"observe", "shadow"}
    return {
        "allowed": allowed,
        "would_allow": would_allow,
        "proposed_decision": decision or "neither",
        "eligible_decision": decision if would_allow else "neither",
        "sizing_multiplier": 1.0 if would_allow else 0.0,
        "reason_codes": reasons,
        "regime_state": state or "unknown",
        "regime_as_of": (regime or {}).get("as_of"),
        "macro_event_window": macro_window if isinstance(macro_window, dict) else {},
        "policy_version": active_policy.policy_version,
        "enforcement_mode": active_policy.mode,
    }


def apply_market_regime_gate_to_manager_result(manager_result: dict[str, Any]) -> dict[str, Any]:
    result = dict(manager_result or {})
    recommendation = result.get("recommendation") if isinstance(result.get("recommendation"), dict) else {}
    market_context = result.get("market_context") if isinstance(result.get("market_context"), dict) else {}
    regime = market_context.get("market_regime") if isinstance(market_context.get("market_regime"), dict) else {}
    result["market_regime_gate"] = evaluate_market_regime_gate(
        proposed_decision=str(recommendation.get("decision") or ""),
        manager_confidence=str(recommendation.get("confidence") or ""),
        regime=regime,
    )
    return result
