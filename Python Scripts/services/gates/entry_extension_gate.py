"""ENTRY_EXTENSION: do not chase a move the stock already made in the proposed direction.

Allows an entry when the stock's prior five-day move, signed by the proposed direction, is at most
zero, or when the manager rated overextension risk low. A missing input that could change the
answer gives an unknown verdict, never a rejection, and an unknown never blocks under enforcement.
Inputs are the manager payload's own five-day return and the manager's answer, both fixed before
this verdict, so nothing after the decision is read.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.core.numbers import safe_float
from services.research.research_promotion import guard_policy, rollout_mode
from services.core.trade_sides import trade_side

OVEREXTENSION_LEVELS = {'low', 'medium', 'high'}


@dataclass(frozen=True)
class EntryExtensionPolicy:
    policy_version: str = 'entry-extension-v1'
    mode: str = 'shadow'
    maximum_prior_move_pct: float = 0.0
    allowed_overextension: tuple[str, ...] = ('low',)

    @classmethod
    def from_env(cls) -> 'EntryExtensionPolicy':
        return guard_policy(cls(mode=rollout_mode('ENTRY_EXTENSION_MODE')), {'mode': 'ENTRY_EXTENSION'})


def prior_five_day_move(manager_result: dict[str, Any]) -> float | None:
    """The stock's five-day return before the decision, from the strategist payload; the one definition."""
    return safe_float(((manager_result.get('company') or {}).get('trend_quality') or {}).get('5d_return_pct'))


def overextension_level(recommendation: dict[str, Any]) -> str:
    level = str(recommendation.get('overextension_risk') or '').strip().lower()
    return level if level in OVEREXTENSION_LEVELS else 'unknown'


DEFAULT_POLICY = EntryExtensionPolicy()


def extension_verdict(signed_move: float | None, overextension: str,
                      policy: EntryExtensionPolicy = DEFAULT_POLICY) -> tuple[bool | None, str]:
    """(would_allow, reason) for a move already signed by the trade; None when a missing input could change it."""
    if signed_move is not None and signed_move <= policy.maximum_prior_move_pct:
        return True, 'prior_move_not_extended'
    if overextension in policy.allowed_overextension:
        return True, 'manager_overextension_low'
    if signed_move is None:
        return None, 'prior_5d_move_unavailable'
    if overextension == 'unknown':
        return None, 'manager_overextension_unavailable'
    return False, 'extended_prior_move_without_low_overextension'


def evaluate_entry_extension_gate(manager_result: dict[str, Any], policy: EntryExtensionPolicy | None = None) -> dict[str, Any]:
    policy = policy or EntryExtensionPolicy.from_env()
    recommendation = manager_result.get('recommendation') or {}
    decision = str(recommendation.get('decision') or '').strip().lower()
    overextension = overextension_level(recommendation)
    move, side = prior_five_day_move(manager_result), trade_side(decision)
    signed = None if move is None or side is None else side * move

    if side is None:
        would_allow, reason = None, 'entry_extension_requires_directional_decision'
    else:
        would_allow, reason = extension_verdict(signed, overextension, policy)

    return {
        'allowed': would_allow is not False or policy.mode != 'enforce',
        'would_allow': would_allow,
        'proposed_decision': decision or 'neither',
        'reason_codes': [reason],
        'prior_5d_move_pct': move,
        'signed_prior_move_pct': signed,
        'overextension_risk': overextension,
        'policy_version': policy.policy_version,
        'enforcement_mode': policy.mode,
    }
