"""Trade-economics reports, the TRADE_ECONOMICS entry gate and the submission recheck.

The manager's direction and DTE bucket are a proposal. This prices the side-matching
contracts that the bucket refresh hydrated, using dated empirical catalyst scenarios, and
judges whether the selected contract is worth buying after declared costs. It never picks a
different contract; the deterministic selector owns selection.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import logging
import math

from catalyst_models import timestamp
from news_db_modules.catalysts import get_catalyst_revision
from market_db_modules.foundation_store import digest
from market_db_modules.pricing_research import catalyst_return_scenarios
from market_db_modules.trade_economics import get_candidate_economics_report, record_candidate_economics_report
from services.evaluation.evaluation_clock import evaluation_now
from services.runtime.execution_telemetry import quote_values
from services.options.option_dte_buckets import get_dte_bucket, resolve_dte_bucket_for_days
from services.options.option_pricing import DAYS_PER_YEAR, CalibratedOption, days_until_expiration, option_side
from services.gates.pricing_policy import RESPONSE_HORIZONS
from services.gates.trade_economics_policy import TradeEconomicsPolicy
from services.core.numbers import finite_float


LOGGER = logging.getLogger(__name__)
REPORT_VERSION = 'trade-economics-report-v1'
FORECAST_VERSION = 'empirical-catalyst-total-return-v1'
DIRECTIONAL_DECISIONS = ('call', 'put')
CONTRACT_MULTIPLIER = 100
MINIMUM_SCENARIO_HORIZON_DAYS = 1
QUANTILES = (.05, .10, .25, .50, .75, .90, .95)
SPOT_NUMBERS = ('price', 'latest_trade_price', 'bid_price', 'ask_price')
SPOT_TEXT = ('source', 'latest_trade_timestamp', 'timestamp')
NO_SCENARIOS = {'status': 'no_catalyst_family', 'sample_size': 0, 'effective_sample_size': 0}
QUOTE_REASONS = {'stale_quote': 'economics_quote_stale', 'invalid_quote': 'economics_quote_invalid'}
MODEL_ASSUMPTIONS = {
    'pricing_model': 'black_scholes_european',
    'volatility': 'IV calibrated to the quote mid, shifted by the declared base IV change in every scenario',
    'exercise': 'European; contracts deeper in the money than maximum_itm_fraction are unsupported',
    'dividends': 'not modelled; the calibrated IV absorbs them at entry only',
    'entry': 'market order filled at the quoted ask plus one fee',
    'exit': 'scenario model value less the entry half-spread, at the holding horizon, plus one fee',
    'execution_cost': 'crossing the quoted spread is the whole execution cost; no measured slippage is added',
    'unsold_option': 'an exit worth no more than its fee is held to expiry and assumed worthless',
    'holding_horizon': "hold until the bucket's forced-exit DTE, snapped down to a stored response horizon",
    'scenario_weights': 'equal weight per independent event cluster: same event day, or same ticker within the horizon',
    'systematic_return': "total stock returns of past catalysts, including their own periods' market and sector moves",
}
LIMITATIONS = (
    'Path-dependent exits (stop loss, take profit, trailing stop, scale-outs, thesis exits) are not '
    'modelled; every scenario exits at the holding horizon.',
    'Scenarios are stratified by catalyst family and horizon, not by market regime.',
    'One IV change applies to every scenario; IV is not modelled jointly with the size of the move.',
    'Live entries are market orders: partial fills and slippage beyond the quote are not modelled, '
    'and no maximum entry price is enforced at the broker.',
)


def directional_decision(manager_result):
    decision = str((manager_result.get('recommendation') or {}).get('decision') or '').strip().lower()
    return decision if decision in DIRECTIONAL_DECISIONS else None


def selected_row(report):
    return next((row for row in report.get('contracts') or [] if row.get('selected')), None)


def horizon_key(horizon_days):
    return format(horizon_days, 'g')


# --- Scenarios -------------------------------------------------------------------------

def independence_clusters(rows, horizon_days):
    """Same event day shares a market move; same ticker within the horizon shares a path."""
    parent = list(range(len(rows)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def join(left, right):
        parent[find(left)] = find(right)

    first_by_day = {}
    for index, row in enumerate(rows):
        join(index, first_by_day.setdefault(row['event_time'].date(), index))
    by_ticker = sorted(range(len(rows)), key=lambda index: (rows[index]['ticker'], rows[index]['event_time']))
    for left, right in zip(by_ticker, by_ticker[1:]):
        same_ticker = rows[left]['ticker'] == rows[right]['ticker']
        if same_ticker and rows[right]['event_time'] - rows[left]['event_time'] < timedelta(days=horizon_days):
            join(left, right)
    roots = [find(index) for index in range(len(rows))]
    labels = {root: label for label, root in enumerate(dict.fromkeys(roots))}
    return [labels[root] for root in roots]


def scenario_set(rows, horizon_days):
    """Each independent cluster gets equal weight, shared across its catalysts."""
    usable = []
    for row in rows:
        total, residual, event_time = finite_float(row.get('stock_return')), finite_float(row.get('residual_return')), timestamp(row.get('event_at'))
        if event_time and total is not None and residual is not None and min(total, residual) > -1:
            usable.append({**row, 'event_time': event_time, 'stock_return': total, 'residual_return': residual})
    clusters = independence_clusters(usable, horizon_days)
    sizes = Counter(clusters)
    return {'status': 'complete' if usable else 'no_samples', 'horizon_days': horizon_days,
            'sample_size': len(usable), 'effective_sample_size': len(sizes),
            'excluded_rows': len(rows) - len(usable),
            'catalyst_ids': [row['catalyst_id'] for row in usable],
            'total_returns': [row['stock_return'] for row in usable],
            'residual_returns': [row['residual_return'] for row in usable],
            'clusters': clusters, 'weights': [1 / (len(sizes) * sizes[label]) for label in clusters]}


def load_scenarios(catalyst, horizon_days, *, as_of, db_path=None):
    if not catalyst.get('catalyst_family'):
        return {**NO_SCENARIOS, 'horizon_days': horizon_days}
    rows = catalyst_return_scenarios(catalyst['catalyst_family'], horizon_days, as_of=as_of,
                                     exclude_catalyst_id=catalyst.get('catalyst_id'), db_path=db_path)
    return scenario_set(rows, horizon_days)


def weighted_quantile(values, weights, fraction):
    cumulative = 0.0
    ordered = sorted(zip(values, weights))
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= fraction - 1e-12:
            return value
    return ordered[-1][0]


def weighted_mean(values, weights):
    return sum(value * weight for value, weight in zip(values, weights))


# --- Contract pricing ------------------------------------------------------------------

def frozen_contract(contract):
    """The inputs a recheck or replay needs, with non-finite numbers stored as unknown."""
    return {'symbol': contract.get('symbol'), 'option_id': contract.get('option_id'),
            'contract_type': str(contract.get('contract_type') or ''),
            'expiration_date': str(contract.get('expiration_date') or ''),
            'strike_price': finite_float(contract.get('strike_price')),
            'implied_volatility': finite_float(contract.get('implied_volatility'))}


def frozen_spot(snapshot):
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    return {**{key: finite_float(snapshot.get(key)) for key in SPOT_NUMBERS},
            **{key: str(snapshot.get(key) or '') for key in SPOT_TEXT}}


def holding_rule(days_to_expiry, target_dte_bucket):
    """Hold until the bucket's forced-exit DTE. Scenarios exist only at stored horizons."""
    manager_bucket = get_dte_bucket(target_dte_bucket)
    # Without a manager bucket, the bucket live exits would choose for this entry DTE.
    bucket = manager_bucket or resolve_dte_bucket_for_days(math.floor(days_to_expiry), prefer_higher_boundary=True)
    if bucket is None or bucket.force_exit_days_to_expiration is None:
        return {'status': 'no_holding_rule'}
    holding_days = days_to_expiry - bucket.force_exit_days_to_expiration
    rule = {'dte_bucket': bucket.key, 'bucket_source': 'manager' if manager_bucket else 'contract_dte',
            'force_exit_days_to_expiration': bucket.force_exit_days_to_expiration, 'holding_days': holding_days}
    horizons = [value for value in RESPONSE_HORIZONS if MINIMUM_SCENARIO_HORIZON_DAYS <= value <= holding_days]
    if not horizons:
        return {**rule, 'status': 'holding_period_too_short'}
    return {**rule, 'status': 'complete', 'scenario_horizon_days': max(horizons)}


def entry_quote(contract, *, as_of, maximum_age_seconds):
    quote = contract.get('latest_quote') or {}
    bid, ask, mid, spread = quote_values(quote)
    quoted_at = timestamp(quote.get('timestamp'))
    age = (as_of - quoted_at).total_seconds() if quoted_at else None
    fresh = age is not None and 0 <= age <= maximum_age_seconds
    status = 'invalid_quote' if mid is None else 'complete' if fresh else 'stale_quote'
    return {'status': status, 'bid': bid, 'ask': ask, 'mid': mid, 'spread': spread,
            'quoted_at': str(quote.get('timestamp') or ''), 'age_seconds': age}


def row_option(row, policy):
    """The calibrated option a priced row describes, rebuilt from its stored fields."""
    return CalibratedOption(row['side'], row['spot'], row['strike'], row['days_to_expiry'] / DAYS_PER_YEAR,
                            row['calibrated_iv'], policy.risk_free_rate)


def exit_proceeds(option, row, move, *, volatility_change):
    """Dollars received per contract when selling at the horizon, before the exit fee."""
    value = option.value_after(row['scenario_horizon_days'], move=move, volatility_change=volatility_change)
    return max(0.0, value - row['entry']['spread'] / 2) * CONTRACT_MULTIPLIER


def exit_net(proceeds, policy):
    return proceeds - policy.fee_per_contract_side if proceeds > policy.fee_per_contract_side else 0.0


def net_pnl(proceeds, *, ask, policy):
    return exit_net(proceeds, policy) - ask * CONTRACT_MULTIPLIER - policy.fee_per_contract_side


def priced_inputs(contract, *, side, spot, as_of, target_dte_bucket, policy, maximum_quote_age_seconds):
    """Everything one contract needs before scenarios; a non-complete status stops there."""
    row = {'option_symbol': str(contract.get('symbol') or '').upper(), 'side': side, 'spot': spot,
           'contract': frozen_contract(contract)}
    entry = entry_quote(contract, as_of=as_of, maximum_age_seconds=maximum_quote_age_seconds)
    row['entry'] = entry
    strike = finite_float(contract.get('strike_price'))
    days = days_until_expiration(contract.get('expiration_date'), as_of=as_of)
    if entry['status'] != 'complete':
        return {**row, 'status': entry['status']}
    if not strike or strike <= 0 or days is None or not spot or spot <= 0 or option_side(contract.get('contract_type')) != side:
        return {**row, 'status': 'missing_contract_inputs'}
    holding = holding_rule(days, target_dte_bucket)
    moneyness = (spot - strike) / spot if side == 'call' else (strike - spot) / spot
    row.update(strike=strike, days_to_expiry=days, holding=holding, in_the_money_fraction=moneyness)
    if holding['status'] != 'complete':
        return {**row, 'status': holding['status']}
    if moneyness > policy.maximum_itm_fraction:
        return {**row, 'status': 'unsupported_exercise_assumption'}
    option = CalibratedOption.from_mid(side, entry['mid'], spot=spot, strike=strike, years=days / DAYS_PER_YEAR,
                                       rate=policy.risk_free_rate)
    row['quoted_iv'] = finite_float(contract.get('implied_volatility'))
    if option is None:
        return {**row, 'calibrated_iv': None, 'status': 'iv_calibration_failed'}
    row['calibrated_iv'] = option.volatility
    if option.volatility + policy.base_iv_change <= 0:
        return {**row, 'status': 'invalid_iv_scenario'}
    return {**row, 'status': 'complete', 'scenario_horizon_days': holding['scenario_horizon_days'],
            'exit_iv': option.volatility + policy.base_iv_change}


def cost_breakdown(row, option, policy):
    """Dollar costs per contract at an unchanged spot, so each source can be read alone."""
    entry = row['entry']
    decayed = option.value_after(row['scenario_horizon_days'])
    shifted = option.value_after(row['scenario_horizon_days'], volatility_change=policy.base_iv_change)
    return {'entry_half_spread': (entry['ask'] - entry['mid']) * CONTRACT_MULTIPLIER,
            'exit_half_spread': entry['spread'] / 2 * CONTRACT_MULTIPLIER,
            'fees_round_trip': 2 * policy.fee_per_contract_side,
            'time_decay_unchanged_spot': (entry['mid'] - decayed) * CONTRACT_MULTIPLIER,
            'iv_change_unchanged_spot': (decayed - shifted) * CONTRACT_MULTIPLIER,
            'slippage_beyond_quote': 0.0}


def scenario_outcomes(row, option, scenarios, policy):
    """Expected values only when independent clusters reach the declared minimum."""
    ask, weights = row['entry']['ask'], scenarios['weights']
    proceeds = [exit_proceeds(option, row, move, volatility_change=policy.base_iv_change)
                for move in scenarios['total_returns']]
    pnl = [net_pnl(value, ask=ask, policy=policy) for value in proceeds]
    capital = ask * CONTRACT_MULTIPLIER + policy.fee_per_contract_side
    outcomes = {'capital_at_risk': capital, 'scenario_count': len(pnl),
                'effective_sample_size': scenarios['effective_sample_size'],
                'scenario_range': {'minimum': min(pnl), 'maximum': max(pnl), 'basis': 'unweighted observed scenarios'},
                'expected_value_available': scenarios['effective_sample_size'] >= policy.minimum_effective_samples}
    if not outcomes['expected_value_available']:
        return {**outcomes, 'expected_value_unavailable_reason': 'effective_sample_below_minimum',
                'expected_net_pnl': None, 'expected_net_return': None, 'pnl_quantiles': None,
                'profitable_scenario_weight': None, 'break_even_entry_price': None,
                'residual_only_expected_net_pnl': None}
    residual = [net_pnl(exit_proceeds(option, row, move, volatility_change=policy.base_iv_change), ask=ask, policy=policy)
                for move in scenarios['residual_returns']]
    expected = weighted_mean(pnl, weights)
    expected_exit = weighted_mean([exit_net(value, policy) for value in proceeds], weights)
    return {**outcomes, 'expected_net_pnl': expected, 'expected_net_return': expected / capital,
            'pnl_quantiles': {f'p{round(q * 100):02d}': weighted_quantile(pnl, weights, q) for q in QUANTILES},
            'profitable_scenario_weight': sum(weight for value, weight in zip(pnl, weights) if value > 0),
            'break_even_entry_price': (expected_exit - policy.fee_per_contract_side) / CONTRACT_MULTIPLIER,
            'residual_only_expected_net_pnl': weighted_mean(residual, weights)}


def stress_grid(row, option, policy):
    """Unweighted IV-implied moves and declared IV shifts; no probability is attached."""
    sigma = option.volatility * math.sqrt(row['scenario_horizon_days'] / DAYS_PER_YEAR)
    grid = []
    for multiple in policy.stress_sigma_multiples:
        for change in policy.stress_iv_changes:
            move = multiple * sigma
            if move <= -1 or option.volatility + change <= 0:
                continue
            proceeds = exit_proceeds(option, row, move, volatility_change=change)
            grid.append({'sigma_multiple': multiple, 'underlying_move': move, 'iv_change': change,
                         'net_pnl': net_pnl(proceeds, ask=row['entry']['ask'], policy=policy)})
    return grid


def evaluate_contract(contract, *, side, spot, as_of, target_dte_bucket, scenarios_for, policy, maximum_quote_age_seconds):
    row = priced_inputs(contract, side=side, spot=spot, as_of=as_of, target_dte_bucket=target_dte_bucket,
                        policy=policy, maximum_quote_age_seconds=maximum_quote_age_seconds)
    if row['status'] != 'complete':
        return row
    scenarios = scenarios_for(row['scenario_horizon_days'])
    if not scenarios['sample_size']:
        return {**row, 'status': 'no_scenarios', 'scenario_status': scenarios['status']}
    option = row_option(row, policy)
    return {**row, 'costs': cost_breakdown(row, option, policy),
            'outcomes': scenario_outcomes(row, option, scenarios, policy), 'stress': stress_grid(row, option, policy)}


# --- Report ----------------------------------------------------------------------------

def report_contracts(market_context, selected, *, side, limit):
    """Side-matching contracts from the bucket-refreshed chain, always including the selection."""
    contracts = ((market_context.get('option_market') or {}).get('contracts')) or []
    matching = [contract for contract in contracts if option_side(contract.get('contract_type')) == side]
    symbol = str((selected or {}).get('symbol') or '').upper()
    chosen = [contract for contract in matching if str(contract.get('symbol') or '').upper() == symbol] or (
        [selected] if symbol else [])
    others = [contract for contract in matching if str(contract.get('symbol') or '').upper() != symbol]
    return chosen + others[:max(0, limit - len(chosen))]


def report_catalyst(candidate_context, *, db_path=None):
    state = ((candidate_context or {}).get('catalysts') or [{}])[0] or {}
    catalyst = {key: state.get(key) for key in ('catalyst_id', 'catalyst_family', 'direction', 'expected_horizon')}
    try:
        catalyst['revision_at_report'] = get_catalyst_revision(catalyst['catalyst_id'], db_path=db_path) \
            if catalyst['catalyst_id'] else None
    except Exception as error:
        # The submission recheck reports an unknown revision as unverifiable.
        LOGGER.warning('Could not read the revision of catalyst %s: %s', catalyst['catalyst_id'], error)
        catalyst['revision_at_report'] = None
    return catalyst


def report_verdict(selected, policy):
    if selected is None:
        return 'unavailable', ['economics_no_selected_contract']
    if selected['status'] != 'complete':
        return 'unavailable', ['economics_selected_contract_unavailable']
    outcomes = selected['outcomes']
    if not outcomes['expected_value_available']:
        return 'unavailable', ['economics_expected_value_unavailable']
    if outcomes['expected_net_return'] <= policy.minimum_expected_net_return:
        return 'no_trade', ['economics_expected_return_below_minimum']
    return 'trade', []


def coverage_status(rows, selected):
    priced = [row for row in rows if row['status'] == 'complete']
    if not priced:
        return 'unavailable'
    complete = len(priced) == len(rows) and selected is not None and selected['status'] == 'complete' \
        and selected['outcomes']['expected_value_available']
    return 'complete' if complete else 'partial'


def build_trade_economics_report(manager_result, *, policy, as_of, db_path=None):
    from agent_helpers.deterministic_option_selector.market import _get_reference_stock_price
    side = directional_decision(manager_result)
    context = manager_result.get('candidate_context') or {}
    market = manager_result.get('market_context') or {}
    recommendation = manager_result.get('recommendation') or {}
    selected = manager_result.get('selected_option') or {}
    catalyst = report_catalyst(context, db_path=db_path)
    spot = _get_reference_stock_price(market)
    scenarios = {}

    def scenarios_for(horizon):
        if horizon not in scenarios:
            scenarios[horizon] = load_scenarios(catalyst, horizon, as_of=as_of, db_path=db_path)
        return scenarios[horizon]

    target_bucket = recommendation.get('target_dte_bucket')
    rows = []
    for contract in report_contracts(market, selected, side=side, limit=policy.maximum_contracts):
        rows.append(evaluate_contract(contract, side=side, spot=spot, as_of=as_of, target_dte_bucket=target_bucket,
                                      scenarios_for=scenarios_for, policy=policy,
                                      maximum_quote_age_seconds=policy.maximum_input_quote_age_seconds))
    selected_symbol = str(selected.get('symbol') or '').upper() or None
    for row in rows:
        row['selected'] = row['option_symbol'] == selected_symbol
    chosen = next((row for row in rows if row['selected']), None)
    outcome, reasons = report_verdict(chosen, policy)
    absorption = (context.get('price_absorption_gate') or {}).get('metrics') or {}
    underlying = {'price': spot, 'atr_fraction': finite_float(absorption.get('atr_fraction')),
                  'snapshot': frozen_spot(market.get('current_stock_price'))}
    return {
        'schema_version': REPORT_VERSION, 'forecast_version': FORECAST_VERSION,
        'candidate_id': context.get('candidate_id'),
        'symbol': str(context.get('symbol') or (manager_result.get('company') or {}).get('symbol') or '').upper(),
        'company_id': (manager_result.get('company') or {}).get('company_id'),
        'package_id': (manager_result.get('company_evidence') or {}).get('package_id'),
        'created_at': as_of.isoformat(),
        'valid_until': (as_of + timedelta(seconds=policy.report_validity_seconds)).isoformat(),
        'proposal': {'decision': side, 'target_dte_bucket': target_bucket,
                     'manager_confidence': recommendation.get('confidence')},
        'catalyst': catalyst, 'underlying': underlying, 'selected_option_symbol': selected_symbol,
        'quote_snapshot_hash': digest({'underlying': underlying,
                                       'quotes': {row['option_symbol']: row['entry'] for row in rows}}),
        'contracts': rows, 'scenarios': {horizon_key(key): value for key, value in scenarios.items()},
        'assumptions': {'policy': policy.assumptions(), 'model': MODEL_ASSUMPTIONS},
        'limitations': list(LIMITATIONS), 'coverage_status': coverage_status(rows, chosen),
        'outcome': outcome, 'reason_codes': reasons,
    }


# --- Gate ------------------------------------------------------------------------------

def economics_explanation(report, selected):
    """The deterministic finalizer's account of the decision, from the report alone."""
    if report['outcome'] == 'unavailable':
        detail = selected['status'] if selected and selected['status'] != 'complete' else report['reason_codes'][0]
        return f"Trade economics unavailable ({detail}); no expected value supports this entry."
    outcomes = selected['outcomes']
    quantiles = outcomes['pnl_quantiles']
    text = (f"{selected['option_symbol']}: expected net P&L ${outcomes['expected_net_pnl']:.2f} "
            f"({outcomes['expected_net_return']:.1%} of ${outcomes['capital_at_risk']:.2f} at risk) over "
            f"{selected['scenario_horizon_days']:g} days from {outcomes['effective_sample_size']} independent "
            f"catalyst clusters; 10th percentile ${quantiles['p10']:.2f}.")
    if report['outcome'] == 'no_trade':
        return text + ' The option is unattractive after costs even if the thesis holds.'
    return text


def trade_economics_gate(report, *, report_id, policy):
    reasons = list(report['reason_codes']) + ([] if report_id else ['economics_report_unpersisted'])
    decision = report['proposal']['decision']
    would_allow = not reasons
    allowed = would_allow or policy.mode == 'shadow'
    selected = selected_row(report)
    outcomes = (selected or {}).get('outcomes') or {}
    return {'stage': 'TRADE_ECONOMICS', 'allowed': allowed, 'would_allow': would_allow,
            'proposed_decision': decision, 'eligible_decision': decision if allowed else 'neither',
            'reason_codes': reasons, 'policy_version': policy.policy_version, 'enforcement_mode': policy.mode,
            'report_id': report_id, 'package_id': report['package_id'], 'valid_until': report['valid_until'],
            'selected_option_symbol': report['selected_option_symbol'], 'outcome': report['outcome'],
            'evidence': {'coverage_status': report['coverage_status'],
                         'scenario_horizon_days': (selected or {}).get('scenario_horizon_days'),
                         **{key: outcomes.get(key) for key in ('expected_net_pnl', 'expected_net_return',
                                                               'capital_at_risk', 'effective_sample_size',
                                                               'break_even_entry_price')}},
            'summary': economics_explanation(report, selected)}


def economics_error_gate(decision, policy):
    return {'stage': 'TRADE_ECONOMICS', 'allowed': policy.mode == 'shadow', 'would_allow': False,
            'proposed_decision': decision, 'eligible_decision': decision if policy.mode == 'shadow' else 'neither',
            'reason_codes': ['economics_check_error'], 'policy_version': policy.policy_version,
            'enforcement_mode': policy.mode, 'report_id': None, 'outcome': 'unavailable'}


def persist_report(report, *, db_path=None):
    if not report['candidate_id']:
        return None
    from services.runtime.candidate_lifecycle import observe
    return observe(record_candidate_economics_report, report, db_path=db_path)


def apply_trade_economics(manager_result, *, policy, as_of=None, db_path=None):
    """Attach the gate to every directional result while the feature is on.

    A failure becomes a blocking verdict rather than a missing one, because a missing optional
    gate would leave entry eligibility unchanged.
    """
    decision = directional_decision(manager_result)
    if policy.mode == 'off' or decision is None:
        return manager_result
    now = as_of or evaluation_now()
    try:
        report = build_trade_economics_report(manager_result, policy=policy, as_of=now, db_path=db_path)
    except Exception:
        LOGGER.exception('Trade-economics report failed for %s', manager_result.get('candidate_id'))
        return {**manager_result, 'trade_economics_gate': economics_error_gate(decision, policy)}
    report_id = persist_report(report, db_path=db_path)
    return {**manager_result, 'trade_economics_gate': trade_economics_gate(report, report_id=report_id, policy=policy)}


# --- Submission recheck ----------------------------------------------------------------

def underlying_reasons(report, arrival_underlying, *, now, policy):
    # The arrival snapshot's `price` is the stock ask; the midpoint matches the decision spot better.
    price = finite_float(arrival_underlying.get('mid_price')) or finite_float(arrival_underlying.get('price'))
    quoted_at = timestamp(arrival_underlying.get('timestamp'))
    age = (now - quoted_at).total_seconds() if quoted_at else None
    metrics = {'arrival_spot': price, 'arrival_spot_age_seconds': age, 'report_spot': report['underlying']['price']}
    if price is None or price <= 0 or age is None or not 0 <= age <= policy.maximum_submission_quote_age_seconds:
        return ['economics_underlying_stale'], metrics
    atr_fraction, decision_spot = report['underlying']['atr_fraction'], report['underlying']['price']
    if not atr_fraction or not decision_spot:
        return [], {**metrics, 'underlying_move_check': 'atr_unavailable'}
    allowed_move = policy.maximum_underlying_move_atr * atr_fraction * decision_spot
    metrics.update(underlying_move=price - decision_spot, allowed_underlying_move=allowed_move)
    return (['economics_underlying_moved'] if abs(price - decision_spot) > allowed_move else []), metrics


def event_reasons(report, *, db_path=None):
    catalyst = report['catalyst']
    if not catalyst.get('catalyst_id'):
        return [], {}
    current = get_catalyst_revision(catalyst['catalyst_id'], db_path=db_path)
    metrics = {'revision_at_report': catalyst.get('revision_at_report'), 'revision_now': current}
    if catalyst.get('revision_at_report') is None or current is None:
        return ['economics_event_unverifiable'], metrics
    return (['economics_event_revised'] if current != catalyst['revision_at_report'] else []), metrics


def repricing_reasons(report, arrival_option, *, spot, now, policy):
    """Re-run the selected contract on the frozen scenarios with the arrival quote and spot."""
    selected = selected_row(report)
    stored = report['scenarios']
    row = evaluate_contract({**selected['contract'], 'latest_quote': arrival_option},
                            side=report['proposal']['decision'], spot=spot, as_of=now,
                            target_dte_bucket=report['proposal']['target_dte_bucket'],
                            scenarios_for=lambda horizon: stored.get(horizon_key(horizon), NO_SCENARIOS),
                            policy=policy, maximum_quote_age_seconds=policy.maximum_submission_quote_age_seconds)
    outcomes = row.get('outcomes') or {}
    metrics = {'repriced_status': row['status'], 'arrival_ask': row['entry']['ask'],
               'repriced_expected_net_pnl': outcomes.get('expected_net_pnl'),
               'repriced_expected_net_return': outcomes.get('expected_net_return')}
    if row['status'] != 'complete':
        return [QUOTE_REASONS.get(row['status'], 'economics_recheck_unavailable')], metrics
    if not outcomes['expected_value_available']:
        return ['economics_expected_value_unavailable'], metrics
    if outcomes['expected_net_return'] <= policy.minimum_expected_net_return:
        return ['economics_edge_exhausted'], metrics
    return [], metrics


def submission_checks(candidate, gate, arrival, *, now, db_path=None):
    if not gate.get('report_id'):
        return ['economics_report_missing'], {}
    try:
        report = get_candidate_economics_report(gate['report_id'], db_path=db_path)
    except ValueError:
        return ['economics_report_missing'], {}
    policy = TradeEconomicsPolicy.from_assumptions(report['assumptions']['policy'])
    reasons, metrics = [], {'report_id': gate['report_id'], 'valid_until': report['valid_until']}
    if str(candidate.get('selected_option_symbol') or '').upper() != report['selected_option_symbol']:
        reasons.append('economics_contract_mismatch')
    if (selected_row(report) or {}).get('status') != 'complete':
        return reasons + ['economics_selected_contract_unavailable'], metrics
    if now > timestamp(report['valid_until']):
        reasons.append('economics_report_expired')
    arrival = arrival if isinstance(arrival, dict) else {}
    for check in (lambda: underlying_reasons(report, arrival.get('underlying') or {}, now=now, policy=policy),
                  lambda: event_reasons(report, db_path=db_path)):
        found, details = check()
        reasons.extend(found)
        metrics.update(details)
    spot = metrics.get('arrival_spot')
    if spot and 'economics_underlying_stale' not in reasons:
        found, details = repricing_reasons(report, arrival.get('option') or {}, spot=spot, now=now, policy=policy)
        reasons.extend(found)
        metrics.update(details)
    return reasons, metrics


def recheck_trade_economics(candidate, arrival, *, as_of=None, db_path=None):
    """Is the decision-time report still valid at submission? None while the feature is off.

    Enforced candidates must carry a persisted, unexpired report whose event, underlying and
    repriced selected contract still support the entry; shadow candidates only record why not.
    """
    gate = candidate.get('trade_economics_gate')
    if not isinstance(gate, dict):
        if TradeEconomicsPolicy.from_env().mode != 'enforce':
            return None
        gate = {'enforcement_mode': 'enforce'}
    now = as_of or datetime.now(timezone.utc)
    try:
        reasons, metrics = submission_checks(candidate, gate, arrival, now=now, db_path=db_path)
    except Exception:
        LOGGER.exception('Trade-economics submission recheck failed for %s', candidate.get('candidate_id'))
        reasons, metrics = ['economics_recheck_error'], {}
    would_allow = not reasons
    mode = gate.get('enforcement_mode')
    return {'stage': 'TRADE_ECONOMICS_SUBMISSION', 'allowed': would_allow or mode != 'enforce',
            'would_allow': would_allow, 'reason_codes': reasons, 'enforcement_mode': mode,
            'report_id': gate.get('report_id'), 'metrics': metrics}
