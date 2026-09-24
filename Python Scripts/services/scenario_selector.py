"""Auditable empirical Greek scenarios, after the existing hard filters."""
from datetime import datetime, time, timezone
import math
from statistics import mean
from zoneinfo import ZoneInfo

from catalyst_models import timestamp
from services.contract_policy import ContractPolicy
from services.execution_telemetry import number, quote_values
from services.pricing_policy import HORIZON_DAYS, PricingPolicy
from market_db_modules.pricing_research import catalyst_return_distribution, quantile


def score_contract(contract, *, spot, moves, horizon_days, policy=None):
    policy = policy or ContractPolicy.from_env()
    greeks = contract.get('greeks') or {}
    delta, gamma, vega, theta = (number(greeks.get(key)) for key in ('delta', 'gamma', 'vega', 'theta'))
    bid, ask, mid, spread = quote_values(contract.get('latest_quote'))
    spot, horizon_days = number(spot), number(horizon_days)
    missing = {'status': 'missing_scenario_inputs', 'option_symbol': contract.get('symbol')}
    if (not moves or not spot or spot <= 0 or not horizon_days or horizon_days <= 0 or mid is None
            or any(value is None for value in (delta, gamma, vega, theta))):
        return missing
    expiry = str(contract.get('expiration_date') or '')
    iv = number(contract.get('implied_volatility'))
    if iv is None or iv <= 0 or iv + policy.scenario_iv_change <= 0:
        return {**missing, 'status': 'invalid_iv_scenario'}
    pnl = []
    # Alpaca-style quoted theta is signed dollars/day and vega dollars per
    # percentage point. IV values and configured shocks are decimal fractions.
    theta_change = theta * horizon_days
    iv_change = vega * policy.scenario_iv_change * 100
    for move in moves:
        if not math.isfinite(move) or move <= -1:
            return {**missing, 'status': 'invalid_return_scenario'}
        ds = spot * move
        future_mid = max(0.0, mid + delta * ds + .5 * gamma * ds * ds + iv_change + theta_change)
        if not math.isfinite(future_mid):
            return {**missing, 'status': 'nonfinite_scenario_price'}
        # Buy at ask, sell at simulated mid less today's half-spread. Premium
        # cannot become negative and a long option cannot lose more than cost.
        proceeds = max(0.0, future_mid - spread / 2)
        pnl.append((proceeds - ask) * 100)
    expected = mean(pnl)
    capital = ask * 100
    p10, p50, p90 = (quantile(pnl, fraction) for fraction in (.1, .5, .9))
    expected_return = expected / capital
    tail_cost = max(0.0, -p10 / capital)
    return {'status': 'complete', 'option_symbol': contract.get('symbol'), 'option_id': contract.get('option_id'),
            'expiration_date': expiry, 'sample_size': len(pnl), 'expected_pnl': expected,
            'expected_return': expected_return, 'pnl_p10': p10, 'pnl_p50': p50, 'pnl_p90': p90,
            'spread_cost': spread * 100, 'theta_cost': -theta_change * 100,
            'expected_iv_cost': -iv_change * 100, 'tail_cost': tail_cost,
            'total_score': expected_return - policy.scenario_tail_weight * tail_cost,
            'scenario_pnl': pnl, 'iv_change_decimal': policy.scenario_iv_change,
            'horizon_days': horizon_days, 'basis': 'per-contract dollars, 100 multiplier; residual return scenarios',
            'model': 'local Greek approximation; signed theta/day; vega per IV percentage point',
            'market_sector_scenario': 'zero systematic return; not a total-stock forecast'}


def rank_contracts(contracts, *, spot, distribution, horizon_days, policy=None, as_of=None):
    policy = policy or ContractPolicy.from_env()
    now = as_of or datetime.now(timezone.utc)
    scores = []
    for contract in contracts:
        try:
            expiry_at = datetime.combine(datetime.strptime(str(contract.get('expiration_date')), '%Y-%m-%d').date(),
                                         time(16), ZoneInfo('America/New_York'))
            dte = (expiry_at - now).total_seconds() / 86400
        except ValueError:
            dte = 0
        if dte < horizon_days:
            scores.append({'option_symbol': contract.get('symbol'), 'status': 'expiry_before_horizon'})
            continue
        scores.append(score_contract(contract, spot=spot, moves=distribution, horizon_days=horizon_days, policy=policy))
    complete = [(contract, score) for contract, score in zip(contracts, scores) if score['status'] == 'complete']
    complete.sort(key=lambda pair: (-pair[1]['total_score'], str(pair[0].get('symbol'))))
    return (complete[0][0] if complete else None), scores


def prepare_scenario_inputs(result, *, as_of=None, db_path=None):
    context = result.get('candidate_context') or {}
    state = (context.get('catalysts') or [{}])[0]
    if not state.get('catalyst_family'):
        return result
    now = as_of or datetime.now(timezone.utc)
    horizon = HORIZON_DAYS.get(state.get('expected_horizon'), 5)
    rows = catalyst_return_distribution(state['catalyst_family'], horizon, as_of=now,
                                        exclude_catalyst_id=state.get('catalyst_id'),
                                        **({'db_path': db_path} if db_path is not None else {}))
    research = {'as_of': now.isoformat(), 'horizon_days': horizon, 'sample_size': len(rows),
                'minimum_samples': PricingPolicy.from_env().minimum_samples,
                'catalyst_ids': [row['catalyst_id'] for row in rows],
                'returns': [row['residual_return'] for row in rows]}
    return {**result, 'market_context': {**(result.get('market_context') or {}), '_v2_scenario_distribution': research}}


def select_with_scenarios(pool, market_context, *, spot):
    policy = ContractPolicy.from_env()
    research = market_context.get('_v2_scenario_distribution') or {}
    minimum = research.get('minimum_samples', PricingPolicy.from_env().minimum_samples)
    usable = len(research.get('returns') or []) >= minimum
    selected, scores = (None, [])
    if usable and policy.scenario_mode != 'disabled':
        selected, scores = rank_contracts(pool, spot=spot, distribution=research['returns'],
                                          horizon_days=research['horizon_days'], policy=policy,
                                          as_of=timestamp(research.get('as_of')))
    comparison = {'mode': policy.scenario_mode, 'status': 'complete' if selected else 'insufficient_scenarios_or_inputs',
                  'sample_size': research.get('sample_size', 0), 'baseline_option_symbol': pool[0].get('symbol') if pool else None,
                  'scenario_option_symbol': selected.get('symbol') if selected else None,
                  'scores': scores, 'research': research}
    return (selected if selected and policy.scenario_mode == 'enforce' else pool[0]), comparison
