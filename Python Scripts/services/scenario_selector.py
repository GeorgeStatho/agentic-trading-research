"""Auditable empirical scenarios repriced with Black-Scholes, after the existing hard filters."""
import math
from statistics import mean

from catalyst_models import timestamp
from services.contract_policy import ContractPolicy
from services.evaluation_clock import evaluation_now
from services.execution_telemetry import number, quote_values
from services.option_pricing import DAYS_PER_YEAR, CalibratedOption, days_until_expiration, option_side
from services.pricing_policy import HORIZON_DAYS, PricingPolicy
from market_db_modules.pricing_research import catalyst_return_distribution, quantile

CONTRACT_MULTIPLIER = 100


def score_contract(contract, *, spot, moves, horizon_days, as_of, policy=None):
    """Buy at the ask, sell at the horizon at the repriced value less today's half-spread.

    Each scenario is a full Black-Scholes repricing from an IV calibrated to the quote mid, so a
    large move is valued correctly in either direction. A local Greek expansion is not: its
    gamma term made a crash look like a gain on a call.
    """
    policy = policy or ContractPolicy.from_env()
    bid, ask, mid, spread = quote_values(contract.get('latest_quote'))
    spot, horizon_days, strike = number(spot), number(horizon_days), number(contract.get('strike_price'))
    days = days_until_expiration(contract.get('expiration_date'), as_of=as_of)
    side = option_side(contract.get('contract_type'))
    missing = {'status': 'missing_scenario_inputs', 'option_symbol': contract.get('symbol')}
    if (not moves or not spot or spot <= 0 or not horizon_days or horizon_days <= 0 or mid is None
            or not strike or strike <= 0 or days is None or side not in {'call', 'put'}):
        return missing
    if days < horizon_days:
        return {**missing, 'status': 'expiry_before_horizon'}
    if any(not math.isfinite(move) or move <= -1 for move in moves):
        return {**missing, 'status': 'invalid_return_scenario'}
    option = CalibratedOption.from_mid(side, mid, spot=spot, strike=strike, years=days / DAYS_PER_YEAR,
                                       rate=policy.scenario_risk_free_rate)
    if option is None:
        return {**missing, 'status': 'iv_calibration_failed'}
    if option.volatility + policy.scenario_iv_change <= 0:
        return {**missing, 'status': 'invalid_iv_scenario'}
    pnl = []
    for move in moves:
        exit_value = option.value_after(horizon_days, move=move, volatility_change=policy.scenario_iv_change)
        # Premium cannot become negative, so a long option cannot lose more than its cost.
        proceeds = max(0.0, exit_value - spread / 2)
        pnl.append((proceeds - ask) * CONTRACT_MULTIPLIER)
    decayed = option.value_after(horizon_days)
    shifted = option.value_after(horizon_days, volatility_change=policy.scenario_iv_change)
    expected = mean(pnl)
    capital = ask * CONTRACT_MULTIPLIER
    p10, p50, p90 = (quantile(pnl, fraction) for fraction in (.1, .5, .9))
    expected_return = expected / capital
    tail_cost = max(0.0, -p10 / capital)
    return {'status': 'complete', 'option_symbol': contract.get('symbol'), 'option_id': contract.get('option_id'),
            'expiration_date': str(contract.get('expiration_date') or ''), 'sample_size': len(pnl),
            'expected_pnl': expected, 'expected_return': expected_return, 'pnl_p10': p10, 'pnl_p50': p50,
            'pnl_p90': p90, 'spread_cost': spread * CONTRACT_MULTIPLIER,
            'theta_cost': (mid - decayed) * CONTRACT_MULTIPLIER,
            'expected_iv_cost': (decayed - shifted) * CONTRACT_MULTIPLIER, 'tail_cost': tail_cost,
            'total_score': expected_return - policy.scenario_tail_weight * tail_cost,
            'scenario_pnl': pnl, 'calibrated_iv': option.volatility, 'iv_change_decimal': policy.scenario_iv_change,
            'horizon_days': horizon_days, 'basis': 'per-contract dollars, 100 multiplier; residual return scenarios',
            'model': 'Black-Scholes repricing at the horizon; IV calibrated to the quote mid; European, no dividends',
            'market_sector_scenario': 'zero systematic return; not a total-stock forecast'}


def rank_contracts(contracts, *, spot, distribution, horizon_days, policy=None, as_of=None):
    policy = policy or ContractPolicy.from_env()
    now = as_of or evaluation_now()
    scores = [score_contract(contract, spot=spot, moves=distribution, horizon_days=horizon_days, as_of=now, policy=policy)
              for contract in contracts]
    complete = [(contract, score) for contract, score in zip(contracts, scores) if score['status'] == 'complete']
    complete.sort(key=lambda pair: (-pair[1]['total_score'], str(pair[0].get('symbol'))))
    return (complete[0][0] if complete else None), scores


def prepare_scenario_inputs(result, *, as_of=None, db_path=None):
    context = result.get('candidate_context') or {}
    state = (context.get('catalysts') or [{}])[0]
    if not state.get('catalyst_family'):
        return result
    now = as_of or evaluation_now()
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
