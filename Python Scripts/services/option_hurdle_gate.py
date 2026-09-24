"""Shadow expected-move comparison against paired ATM straddles."""
from __future__ import annotations

from datetime import datetime, time
import math
from statistics import mean
from zoneinfo import ZoneInfo

from catalyst_models import timestamp
from services.price_response import positive
from services.pricing_policy import PricingPolicy


def select_atm_research_contracts(contracts, spot):
    """Retain both sides before the execution selector keeps only OTM contracts."""
    spot = positive(spot)
    if spot is None:
        return []
    expiries = {}
    for contract in contracts:
        strike = positive(contract.get('strike_price'))
        if strike:
            expiries.setdefault(str(contract.get('expiration_date') or ''), {}).setdefault(strike, []).append(contract)
    return [contract for strikes in expiries.values()
            for contract in strikes[min(strikes, key=lambda value: (abs(value - spot), value))]]


def quote_mid(contract, *, as_of, policy):
    quote = contract.get('latest_quote') or {}
    bid, ask = positive(quote.get('bid_price')), positive(quote.get('ask_price'))
    quoted_at = timestamp(quote.get('timestamp'))
    if bid is None or ask is None or bid > ask or quoted_at is None:
        return None
    age = (as_of - quoted_at).total_seconds()
    return (bid + ask) / 2 if 0 <= age <= policy.maximum_quote_age_seconds else None


def evaluate_option_hurdle(contracts, *, spot, stats, horizon_days, as_of, policy=None):
    policy = policy or PricingPolicy.from_env()
    spot = positive(spot)
    horizon_days = positive(horizon_days)
    expected = positive(stats.get('expected_abs_residual_move')) if stats.get('usable') else None
    groups = {}
    for contract in contracts:
        expiry = str(contract.get('expiration_date') or '')
        strike = positive(contract.get('strike_price'))
        side = str(contract.get('contract_type') or '').lower().rsplit('.', 1)[-1]
        if strike and side in {'call', 'put'}:
            groups.setdefault(expiry, {}).setdefault(strike, {}).setdefault(side, []).append(contract)
    comparisons = []
    for expiry, strikes in sorted(groups.items()):
        row = {'expiration_date': expiry, 'status': 'missing_atm_pair', 'straddle_implied_move': None,
               'iv_implied_move_1sigma': None, 'implied_move_over_horizon': None,
               'move_edge_ratio': None, 'expected_catalyst_move': expected, 'sample_size': stats.get('n', 0)}
        comparisons.append(row)
        try:
            expiry_at = datetime.combine(datetime.strptime(expiry, '%Y-%m-%d').date(), time(16), ZoneInfo('America/New_York'))
        except ValueError:
            row['status'] = 'invalid_expiry'
            continue
        dte = (expiry_at - as_of).total_seconds() / 86400
        row['dte'] = dte
        if not spot or not horizon_days or dte <= 0 or dte < horizon_days:
            row['status'] = 'invalid_spot_or_horizon'
            continue
        # Establish ATM from listed strikes before looking at quote validity. Do
        # not silently replace a missing ATM quote with a cheap far-OTM straddle.
        strike = min(strikes, key=lambda value: (abs(value - spot), value))
        row['atm_strike'] = strike
        if abs(strike / spot - 1) > policy.maximum_atm_distance:
            continue
        pair = []
        for side in ('call', 'put'):
            valid = [(contract, quote_mid(contract, as_of=as_of, policy=policy))
                     for contract in strikes[strike].get(side, [])]
            valid = [(contract, mid) for contract, mid in valid if mid is not None]
            if valid:
                pair.append(max(valid, key=lambda item: timestamp(item[0]['latest_quote']['timestamp'])))
        if len(pair) != 2:
            continue
        call, put = pair
        straddle = (call[1] + put[1]) / spot
        implied = straddle * math.sqrt(horizon_days / dte)
        ivs = [positive(contract.get('implied_volatility')) for contract, _ in pair]
        iv = mean(ivs) if all(value is not None for value in ivs) else None
        row.update(status='complete' if expected else 'insufficient_catalyst_statistics',
                   atm_call_mid=call[1], atm_put_mid=put[1],
                   call_quote=call[0]['latest_quote'], put_quote=put[0]['latest_quote'],
                   straddle_implied_move=straddle, implied_move_over_horizon=implied,
                   iv_implied_move_1sigma=iv * math.sqrt(horizon_days / 365) if iv else None,
                   move_edge_ratio=expected / implied if expected else None)
    ratios = [row['move_edge_ratio'] for row in comparisons if row['move_edge_ratio'] is not None]
    any_pass = any(ratio >= policy.move_edge_threshold for ratio in ratios)
    # An incomplete chain cannot establish that *no* reasonable expiry has edge.
    would_allow = True if any_pass else False if comparisons and len(ratios) == len(comparisons) else None
    return {'stage': 'OPTION_HURDLE', 'allowed': True, 'would_allow': would_allow,
            'enforcement_mode': 'shadow', 'policy_version': policy.version,
            'reason_codes': ['option_hurdle_unknown' if would_allow is None else
                             'insufficient_move_edge' if not would_allow else 'option_hurdle_pass'],
            'thresholds': {'minimum_move_edge_ratio': policy.move_edge_threshold,
                           'minimum_samples': policy.minimum_samples,
                           'maximum_atm_distance': policy.maximum_atm_distance,
                           'maximum_quote_age_seconds': policy.maximum_quote_age_seconds},
            'metrics': {'spot': spot, 'horizon_days': horizon_days, 'calibration': 'provisional',
                        'expected_catalyst_move': expected, 'sample_size': stats.get('n', 0),
                        'stats': stats, 'expirations': comparisons,
                        'chain_scope': 'available manager option chain; missing pairs remain unknown'}}
