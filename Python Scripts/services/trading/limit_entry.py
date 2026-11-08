"""E1 research state machine. No broker submission until baseline acceptance.

The live worker preserves market entries. Replay drives this deterministic state
machine with recorded quote/fill/cancel events; it never invents a fill from a mid.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR

from services.gates.contract_policy import ContractPolicy
from services.runtime.execution_telemetry import quote_values
from services.gates.liquidity_gate import evaluate_liquidity
from catalyst_models import timestamp
from services.core.numbers import finite_float


def buy_limit_price(target, ceiling, *, small_tick=.05, large_tick=.10, threshold=3.0):
    """Floor to the conservative non-penny tick without exceeding the risk cap."""
    target, ceiling = finite_float(target), finite_float(ceiling)
    if target is None or ceiling is None or min(target, ceiling) <= 0:
        return None
    value = Decimal(str(min(target, ceiling)))
    tick = Decimal(str(large_tick if value >= Decimal(str(threshold)) else small_tick))
    rounded = (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    return float(rounded) if rounded > 0 else None


def entry_invalidation(*, quote, underlying_now, underlying_at_decision, atr_dollars,
                       expected_option_value, limit_price, as_of, underlying_at=None, catalyst_changed=False, policy=None):
    policy = policy or ContractPolicy.from_env()
    liquidity = evaluate_liquidity({'latest_quote': quote}, as_of=as_of, policy=policy)
    reasons = list(liquidity['reason_codes'])
    stock_at = timestamp(underlying_at)
    if stock_at is None or not 0 <= (as_of - stock_at).total_seconds() <= policy.maximum_quote_age_seconds:
        reasons.append('execution_underlying_quote_stale')
    if catalyst_changed:
        reasons.append('execution_catalyst_changed')
    values = [finite_float(value) for value in (underlying_now, underlying_at_decision, atr_dollars, expected_option_value, limit_price)]
    if any(value is None or value <= 0 for value in values):
        reasons.append('execution_invalidation_inputs_missing')
    else:
        current, decision, atr, expected, price = values
        if abs(current - decision) > policy.maximum_underlying_move_atr * atr:
            reasons.append('underlying_move_exceeded_atr')
        if expected - price <= 0:
            reasons.append('expected_edge_exhausted')
    return reasons


@dataclass(frozen=True)
class LimitEntryState:
    quantity: int
    attempt: int = 0
    phase: str = 'ready'
    filled_quantity: int = 0
    order_id: str | None = None


def next_entry_action(state, *, bid, ask, maximum_price, invalidation_reasons=(), cancel_confirmed=False):
    """Pure actions: callers must confirm termination before another submission.

    Fill quantities are cumulative verified executions, not inferred quote touches.
    This function intentionally has no live broker adapter in Part 5.
    """
    if state.quantity <= 0 or state.filled_quantity < 0 or state.filled_quantity > state.quantity:
        raise ValueError('Invalid limit-entry quantities')
    if state.filled_quantity == state.quantity:
        return {'action': 'complete', 'quantity': 0}
    if state.phase in {'working', 'cancel_pending'} and not cancel_confirmed:
        return {'action': 'cancel' if state.phase == 'working' else 'wait_for_cancel',
                'order_id': state.order_id, 'reason_codes': list(invalidation_reasons)}
    if invalidation_reasons or state.attempt >= 4:
        return {'action': 'stop', 'reason_codes': list(invalidation_reasons) or ['attempts_exhausted']}
    bid, ask, _, _ = quote_values({'bid_price': bid, 'ask_price': ask})
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return {'action': 'stop', 'reason_codes': ['invalid_quote']}
    mid, half_spread = (bid + ask) / 2, (ask - bid) / 2
    targets = [mid, mid + .2 * half_spread, mid + .4 * half_spread, maximum_price]
    price = buy_limit_price(targets[state.attempt], min(ask, maximum_price)) if maximum_price else None
    if price is None:
        return {'action': 'stop', 'reason_codes': ['missing_price_ceiling']}
    return {'action': 'submit_limit', 'limit_price': price, 'attempt': state.attempt + 1,
            'quantity': state.quantity - state.filled_quantity}


def observe_entry_plan(candidate, arrival, *, quantity, paper, as_of=None, db_path=None):
    from datetime import datetime, timezone
    from market_db_modules.contract_research import execution_baseline
    from services.options.scenario_selector import score_contract
    from services.gates.pricing_policy import PricingPolicy
    policy = ContractPolicy.from_env()
    now = as_of or datetime.now(timezone.utc)
    market = candidate.get('market_context') or {}
    state = (candidate.get('candidate_context') or {}).get('price_absorption_gate') or {}
    decision = finite_float((market.get('current_stock_price') or {}).get('price') or
                      (market.get('current_stock_price') or {}).get('latest_trade_price'))
    distribution = market.get('_v2_scenario_distribution') or {}
    selected = candidate.get('selected_option') or {}
    arrival = arrival if isinstance(arrival, dict) else {}
    quote = arrival.get('option') or {}
    current = finite_float((arrival.get('underlying') or {}).get('price'))
    score = {}
    if len(distribution.get('returns') or []) >= PricingPolicy.from_env().minimum_samples:
        score = score_contract({**selected, 'latest_quote': quote}, spot=current, moves=distribution['returns'],
                               horizon_days=distribution['horizon_days'], as_of=now, policy=policy)
    bid, ask, mid, _ = quote_values(quote)
    expected_value = ask + score['expected_pnl'] / 100 if score.get('status') == 'complete' and ask else None
    ceiling = min(ask, expected_value) if ask and expected_value and expected_value > 0 else None
    atr_fraction = finite_float((state.get('metrics') or {}).get('atr_fraction'))
    validity_inputs = dict(quote=quote, underlying_now=current, underlying_at_decision=decision,
        atr_dollars=decision * atr_fraction if decision and atr_fraction else None,
        expected_option_value=expected_value, as_of=now,
        underlying_at=(arrival.get('underlying') or {}).get('timestamp'), policy=policy)
    reasons = entry_invalidation(**validity_inputs, limit_price=mid)
    regime = (market.get('market_regime') or {}).get('state')
    baseline = execution_baseline(paper=paper, regime_state=regime, minimum_fills=policy.baseline_minimum_fills,
                                  **({'db_path': db_path} if db_path is not None else {}))
    ladder = []
    for attempt in range(4):
        step_state = LimitEntryState(quantity=quantity, attempt=attempt)
        action = next_entry_action(step_state, bid=bid, ask=ask, maximum_price=ceiling, invalidation_reasons=reasons)
        if action['action'] == 'submit_limit':
            invalid = entry_invalidation(**validity_inputs, limit_price=action['limit_price'])
            if invalid:
                action = next_entry_action(step_state, bid=bid, ask=ask, maximum_price=ceiling, invalidation_reasons=invalid)
        ladder.append(action)
    return {'mode': 'shadow_only', 'baseline': baseline, 'ladder': ladder,
            'attempt_wait_seconds': policy.entry_attempt_seconds, 'invalidation_reasons': reasons,
            'expected_option_value': expected_value, 'maximum_price': ceiling,
            'would_have_filled_later': None, 'missed_trade_opportunity_pnl': None,
            'deployment_status': 'live ladder deferred until baseline and counterfactual acceptance'}
