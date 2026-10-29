"""Turn one production entry into a follower strategy's order. Pure: no broker or DB access."""
from __future__ import annotations

from dataclasses import dataclass
import math

from services.numbers import safe_float
from services.option_dte_buckets import OPTION_DTE_BUCKETS

OPTION_MULTIPLIER = 100


@dataclass(frozen=True)
class OrderLeg:
    symbol: str
    side: str  # 'buy' or 'sell'
    asset: str = 'option'  # 'option' or 'stock'
    ratio_qty: int = 1
    expiration_date: str | None = None


@dataclass(frozen=True)
class OrderPlan:
    strategy: str
    legs: tuple[OrderLeg, ...]
    qty: int
    unit_cost: float  # dollars per spread, contract or share at the quoted entry
    multiplier: int  # 100 for options, 1 for shares

    @property
    def capital(self) -> float:
        return self.qty * self.unit_cost


@dataclass(frozen=True)
class Skip:
    strategy: str
    reason: str


def _quote(contract, field):
    return safe_float((contract.get('latest_quote') or {}).get(field))


def _strike(contract):
    return safe_float(contract.get('strike_price'))


def _units(budget, unit_cost):
    # Every follower takes at least one unit so expensive vehicles are still observed;
    # the report compares return on capital, not dollars.
    return max(1, math.floor(budget / unit_cost))


def _option_leg(contract, side):
    return OrderLeg(contract['symbol'], side, 'option', 1, contract.get('expiration_date'))


def _buy_one_contract(strategy, contract, budget):
    unit_cost = _quote(contract, 'ask_price') * OPTION_MULTIPLIER
    return OrderPlan(strategy, (_option_leg(contract, 'buy'),), _units(budget, unit_cost), unit_cost,
                     OPTION_MULTIPLIER)


def _debit_spread(decision, production, contracts, spot, budget, settings):
    long = contracts.get(production['symbol'], production)
    long_ask, long_strike = _quote(long, 'ask_price'), _strike(long)
    if long_ask is None or long_ask <= 0 or long_strike is None:
        return Skip('debit_spread', 'no_long_quote')
    width = spot * settings.spread_width_pct
    same_series = [c for c in contracts.values() if c.get('contract_type') == decision
                   and c.get('expiration_date') == long.get('expiration_date') and _strike(c) is not None]
    if decision == 'call':
        beyond = [c for c in same_series if _strike(c) >= long_strike + width]
        short = min(beyond, key=_strike, default=None)
    else:
        beyond = [c for c in same_series if _strike(c) <= long_strike - width]
        short = max(beyond, key=_strike, default=None)
    if short is None:
        return Skip('debit_spread', 'no_short_strike')
    short_bid = _quote(short, 'bid_price')
    if short_bid is None or short_bid <= 0:
        return Skip('debit_spread', 'no_short_bid')
    unit_cost = (long_ask - short_bid) * OPTION_MULTIPLIER
    if unit_cost <= 0:
        return Skip('debit_spread', 'invalid_net_debit')
    return OrderPlan('debit_spread', (_option_leg(long, 'buy'), _option_leg(short, 'sell')),
                     _units(budget, unit_cost), unit_cost, OPTION_MULTIPLIER)


def _itm_single(decision, production, contracts, spot, budget, settings):
    in_the_money = [c for c in contracts.values() if c.get('contract_type') == decision
                    and c.get('expiration_date') == production.get('expiration_date')
                    and _strike(c) is not None and (_quote(c, 'ask_price') or 0) > 0
                    and (_strike(c) < spot if decision == 'call' else _strike(c) > spot)]
    deltas = {c['symbol']: safe_float((c.get('greeks') or {}).get('delta')) for c in in_the_money}
    if any(delta is not None for delta in deltas.values()):
        in_the_money = [c for c in in_the_money
                        if deltas[c['symbol']] is not None and abs(deltas[c['symbol']]) >= settings.itm_min_delta]
    if not in_the_money:
        return Skip('itm_single', 'no_itm_strike')
    return _buy_one_contract('itm_single', min(in_the_money, key=lambda c: abs(_strike(c) - spot)), budget)


def _production_bucket(production, chains):
    for key, members in chains['buckets'].items():
        if any(c.get('symbol') == production.get('symbol') for c in members):
            return key
    days = safe_float(production.get('days_to_expiration'))
    for bucket in OPTION_DTE_BUCKETS:
        if days is not None and bucket.min_days <= days <= bucket.max_days:
            return bucket.key
    return None


def _longer_dte(decision, production, chains, budget):
    keys = [bucket.key for bucket in OPTION_DTE_BUCKETS]
    current = _production_bucket(production, chains)
    if current not in keys:
        return Skip('longer_dte', 'unknown_bucket')
    if keys.index(current) + 1 >= len(keys):
        return Skip('longer_dte', 'no_longer_bucket')
    members = [c for c in chains['buckets'].get(keys[keys.index(current) + 1]) or ()
               if c.get('contract_type') == decision and _strike(c) is not None
               and (_quote(c, 'ask_price') or 0) > 0]
    if not members:
        return Skip('longer_dte', 'no_contract_in_bucket')
    expiry = min(c['expiration_date'] for c in members)
    # Keep production's distance out of the money. Both are measured against the same spot,
    # so spot x (strike / spot) is production's strike itself (computing it adds float error).
    target = _strike(production)
    further_otm = 1 if decision == 'call' else -1
    chosen = min((c for c in members if c['expiration_date'] == expiry),
                 key=lambda c: (abs(_strike(c) - target), -further_otm * _strike(c)))
    return _buy_one_contract('longer_dte', chosen, budget)


def _shares(decision, underlying, spot, budget):
    side = 'buy' if decision == 'call' else 'sell'
    return OrderPlan('shares', (OrderLeg(underlying, side, 'stock'),), _units(budget, spot), spot, 1)


OPTION_STRATEGIES = ('debit_spread', 'itm_single', 'longer_dte')


def build_order(strategy, *, decision, underlying, production_contract, chains, spot, budget, settings):
    if strategy not in (*OPTION_STRATEGIES, 'shares'):
        raise ValueError(f'Unknown follower strategy: {strategy}')
    if decision not in ('call', 'put'):
        return Skip(strategy, 'not_directional')
    budget, spot = safe_float(budget), safe_float(spot)
    if budget is None or budget <= 0:
        return Skip(strategy, 'no_budget')
    if spot is None or spot <= 0:
        return Skip(strategy, 'no_spot')
    if strategy == 'shares':
        return _shares(decision, underlying, spot, budget)
    if not chains or not chains.get('available'):
        return Skip(strategy, 'chain_unavailable')
    if strategy == 'longer_dte':
        return _longer_dte(decision, production_contract, chains, budget)
    contracts = {c['symbol']: c for members in chains['buckets'].values() for c in members}
    if strategy == 'debit_spread':
        return _debit_spread(decision, production_contract, contracts, spot, budget, settings)
    return _itm_single(decision, production_contract, contracts, spot, budget, settings)
