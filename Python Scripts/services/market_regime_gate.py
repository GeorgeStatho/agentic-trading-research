from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from services.research_promotion import guard_policy

REGIME_RELEVANCE = {'systemic': 1.00, 'sector': .60, 'idiosyncratic': .25}


@dataclass(frozen=True)
class MarketRegimePolicy:
    policy_version: str = "market-regime-v2-scope"
    mode: str = "shadow"
    maximum_opposition: float = .75

    @classmethod
    def from_env(cls) -> "MarketRegimePolicy":
        mode = str(os.getenv("MARKET_REGIME_GATE_MODE", "shadow")).strip().lower()
        if mode not in {"observe", "shadow", "enforce"}:
            mode = "shadow"
        return guard_policy(cls(mode=mode), {'mode': 'REGIME'})


def evaluate_market_regime_gate(
    *,
    proposed_decision: str,
    manager_confidence: str,
    regime: dict[str, Any],
    policy: MarketRegimePolicy | None = None,
    catalyst_scope: str | None = None,
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

    legacy_would_allow = would_allow
    legacy_reasons = list(reasons)
    scope = str(catalyst_scope or 'unknown').lower()
    relevance = REGIME_RELEVANCE.get(scope, 1.0)
    opposition = (2.0 if state == 'stressed' else 1.0 if state == 'range' or
                  (state == 'trend_up' and decision == 'put') or
                  (state == 'trend_down' and decision == 'call') else 0.0)
    penalty = opposition * relevance
    # Missing regime/scope data never grants an exception. Blackout and the
    # existing confidence requirements remain independent of scope weighting.
    if (decision in {'call', 'put'} and not macro_active and opposition > 0
            and scope in REGIME_RELEVANCE):
        would_allow = penalty < active_policy.maximum_opposition
        reasons = ['regime_scope_opposition_pass' if would_allow else 'regime_scope_opposition_block']

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
        "catalyst_scope": scope,
        "regime_relevance_score": relevance,
        "base_opposition": opposition,
        "regime_opposition_penalty": penalty,
        "maximum_opposition": active_policy.maximum_opposition,
        "legacy_would_allow": legacy_would_allow,
        "legacy_reason_codes": legacy_reasons,
        "scope_changed_verdict": would_allow != legacy_would_allow,
    }


def apply_market_regime_gate_to_manager_result(manager_result: dict[str, Any]) -> dict[str, Any]:
    result = dict(manager_result or {})
    recommendation = result.get("recommendation") if isinstance(result.get("recommendation"), dict) else {}
    market_context = result.get("market_context") if isinstance(result.get("market_context"), dict) else {}
    regime = market_context.get("market_regime") if isinstance(market_context.get("market_regime"), dict) else {}
    catalysts = (result.get('candidate_context') or {}).get('catalysts') or []
    # For mixed-scope evidence retain the strongest relevant regime constraint.
    scope = max((state.get('catalyst_scope', 'unknown') for state in catalysts),
                key=lambda value: REGIME_RELEVANCE.get(value, 1.0), default='unknown')
    result["market_regime_gate"] = evaluate_market_regime_gate(
        proposed_decision=str(recommendation.get("decision") or ""),
        manager_confidence=str(recommendation.get("confidence") or ""),
        regime=regime,
        catalyst_scope=scope,
    )
    return result
