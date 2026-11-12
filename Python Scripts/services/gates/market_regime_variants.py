"""Shadow-only alternatives to the market-regime gate, recorded beside it so research can compare them.

A variant never acts: `allowed` is always True, it has its own stage and result key, and its file is
outside REGIME's proof files, so no reader can take it for the production REGIME verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.core.numbers import safe_float
from services.core.trade_sides import trade_side


@dataclass(frozen=True)
class RegimeSpyTrendPolicy:
    """Block calls only when SPY's 5- and 20-day returns are both down; puts only when both are up."""
    policy_version: str = 'regime-spy-trend-v1'


def evaluate_regime_spy_trend_gate(manager_result: dict[str, Any], policy: RegimeSpyTrendPolicy | None = None) -> dict[str, Any]:
    policy = policy or RegimeSpyTrendPolicy()
    decision = str((manager_result.get('recommendation') or {}).get('decision') or '').strip().lower()
    regime = (manager_result.get('market_context') or {}).get('market_regime') or {}
    signals = regime.get('signals') or {}
    five, twenty = safe_float(signals.get('spy_return_5d_pct')), safe_float(signals.get('spy_return_20d_pct'))
    side = trade_side(decision)
    if side is None:
        would_allow, reason = None, 'regime_variant_requires_directional_decision'
    elif five is None or twenty is None:
        would_allow, reason = None, 'spy_trend_returns_unavailable'
    elif side * five < 0 and side * twenty < 0:  # SPY trends against the trade on both horizons
        would_allow, reason = False, ('spy_5d_and_20d_down_blocks_call' if side > 0
                                      else 'spy_5d_and_20d_up_blocks_put')
    else:
        would_allow, reason = True, 'spy_trend_not_opposed'
    return {
        'allowed': True,
        'would_allow': would_allow,
        'proposed_decision': decision or 'neither',
        'reason_codes': [reason],
        'regime_state': regime.get('state') or 'unknown',
        'spy_return_5d_pct': five,
        'spy_return_20d_pct': twenty,
        'variant_of': 'REGIME',
        'policy_version': policy.policy_version,
        'enforcement_mode': 'shadow',
    }
