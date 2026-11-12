"""A deterministic pick score from decision-time features. Log only: nothing orders or blocks on it.

pick-score-v1 is the mean of whichever of these components are known, each in [0, 1]:
- extension: the candidate's ENTRY_EXTENSION verdict — 1 when it would allow, 0 when it would reject,
  unknown when it has no verdict (so the score must be computed after that gate).
- freshness: FRESHNESS remaining_information_value of the candidate's lead catalyst.
- relative_strength: neutral (0.5) plus the 20-day return relative to SPY, signed in the direction, scaled
  so a RELATIVE_STRENGTH_SPAN_PCT lead scores 1 and the same lag scores 0.
Change the formula only together with PICK_SCORE_VERSION. Ordering same-cycle candidates by it in
trade_executor.py waits until the gate ledger shows it separates outcomes.
"""
from market_db_modules.bootstrap import PICK_CONFIDENCE_COLUMNS
from services.core.numbers import finite_float
from services.core.trade_sides import trade_side

PICK_SCORE_VERSION = 'pick-score-v1'
NEUTRAL_RELATIVE_STRENGTH = 0.5
RELATIVE_STRENGTH_SPAN_PCT = 10.0


def _unit(value):
    return min(1.0, max(0.0, value))


def compute_pick_score(manager_result):
    recommendation = manager_result.get('recommendation') or {}
    history = (manager_result.get('market_context') or {}).get('underlying_price_history') or {}
    side = trade_side(recommendation.get('decision'))
    extension = manager_result.get('entry_extension_gate') or {}
    allowed = extension.get('would_allow')
    risk = recommendation.get('overextension_risk')
    relative = finite_float(((((history.get('technical_indicators') or {}).get('relative_strength') or {})
                              .get('vs_spy') or {}).get('returns') or {}).get('20d', {}).get('relative_return_pct'))
    freshness = (((manager_result.get('candidate_context') or {}).get('catalyst_gates') or {})
                 .get('FRESHNESS') or {}).get('metrics') or {}
    remaining = finite_float(freshness.get('remaining_information_value'))
    components = {'extension': None if allowed is None else float(allowed),
                  'freshness': None if remaining is None else _unit(remaining),
                  'relative_strength': None if relative is None or side is None else round(_unit(
                      NEUTRAL_RELATIVE_STRENGTH + side * relative / (2 * RELATIVE_STRENGTH_SPAN_PCT)), 6)}
    known = [value for value in components.values() if value is not None]
    return {'version': PICK_SCORE_VERSION, 'use': 'log_only',
            'score': sum(known) / len(known) if known and side else None,
            'components': components,
            'inputs': {'prior_5d_move_pct': extension.get('signed_prior_move_pct'), 'overextension_risk': risk,
                       'relative_return_20d_vs_spy_pct': relative, 'remaining_information_value': remaining},
            'confidence': {key: recommendation.get(key) for key in PICK_CONFIDENCE_COLUMNS}}
