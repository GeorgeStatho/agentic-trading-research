"""Quote depth, age and spread checks independent of open-interest ranking."""
from catalyst_models import timestamp
from services.evaluation.evaluation_clock import evaluation_now
from services.runtime.execution_telemetry import quote_values
from services.gates.contract_policy import ContractPolicy
from services.core.numbers import finite_float


def evaluate_liquidity(contract, *, as_of=None, quantity=1, policy=None):
    policy = policy or ContractPolicy.from_env()
    now = as_of or evaluation_now()
    quote = contract.get('latest_quote') or {}
    bid, ask, mid, spread = quote_values(quote)
    quoted = timestamp(quote.get('timestamp'))
    age = (now - quoted).total_seconds() if quoted else None
    bid_size, ask_size = finite_float(quote.get('bid_size')), finite_float(quote.get('ask_size'))
    minimum_size = max(policy.minimum_quote_size, quantity)
    reasons = []
    if mid is None:
        reasons.append('liquidity_invalid_quote')
    if age is None or age < 0 or age > policy.maximum_quote_age_seconds:
        reasons.append('liquidity_stale_quote')
    if bid_size is None or ask_size is None or min(bid_size, ask_size) < minimum_size:
        reasons.append('liquidity_insufficient_depth')
    fraction = spread / mid if mid else None
    if fraction is not None and fraction > policy.maximum_spread_fraction:
        reasons.append('liquidity_wide_spread')
    return {'stage': 'LIQUIDITY', 'allowed': not reasons or policy.liquidity_mode != 'enforce',
            'would_allow': not reasons, 'reason_codes': reasons, 'enforcement_mode': policy.liquidity_mode,
            'metrics': {'bid': bid, 'ask': ask, 'mid': mid, 'spread_pct_mid': fraction,
                        'nbbo_age_seconds': age, 'bid_size': bid_size, 'ask_size': ask_size,
                        'open_interest': contract.get('open_interest'), 'quantity': quantity},
            'thresholds': {'minimum_size': minimum_size, 'maximum_age_seconds': policy.maximum_quote_age_seconds,
                           'maximum_spread_fraction': policy.maximum_spread_fraction}}


def filter_liquid_contracts(result, *, as_of=None, policy=None):
    from services.runtime.candidate_lifecycle import reject
    policy = policy or ContractPolicy.from_env()
    market = dict(result.get('market_context') or {})
    options = dict(market.get('option_market') or {})
    contracts = options.get('contracts') or []
    verdicts = [{'option_symbol': c.get('symbol'), 'option_id': c.get('option_id'),
                 **evaluate_liquidity(c, as_of=as_of, policy=policy)} for c in contracts]
    context = result.get('candidate_context')
    from services.gates.contract_observations import record_contract_observation
    record_contract_observation(context, 'LIQUIDITY', verdicts)
    if policy.liquidity_mode != 'disabled':
        # One canonical rejection per reason, retaining every affected contract.
        for reason in sorted({reason for row in verdicts for reason in row['reason_codes']}):
            affected = [row for row in verdicts if reason in row['reason_codes']]
            reject(context, 'LIQUIDITY', reason, metrics={'contracts': affected},
                   thresholds=affected[0]['thresholds'], shadow=policy.liquidity_mode != 'enforce')
    if policy.liquidity_mode == 'enforce':
        options['contracts'] = [contract for contract, verdict in zip(contracts, verdicts) if verdict['allowed']]
        options['contract_count'] = len(options['contracts'])
        market['option_market'] = options
    return {**result, 'market_context': market, 'liquidity_gate': {'mode': policy.liquidity_mode, 'contracts': verdicts}}
