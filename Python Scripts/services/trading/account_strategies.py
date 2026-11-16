"""Turn one production entry into a follower strategy's order. Pure: no broker or DB access."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math

from services.core.numbers import safe_float

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
    delta: float | None = None  # per unit, signed; None when a leg's greek is missing

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


def _delta(contract):
    return safe_float((contract.get('greeks') or {}).get('delta'))


def _expiry(contract):
    try:
        return date.fromisoformat(str(contract.get('expiration_date') or ''))
    except ValueError:
        return None


def _units(budget, unit_cost):
    # Every follower takes at least one unit so expensive vehicles are still observed;
    # the report compares return on capital, not dollars.
    return max(1, math.floor(budget / unit_cost))


def _option_leg(contract, side):
    return OrderLeg(contract['symbol'], side, 'option', 1, contract.get('expiration_date'))


def _buy_one_contract(strategy, contract, budget):
    unit_cost = _quote(contract, 'ask_price') * OPTION_MULTIPLIER
    return OrderPlan(strategy, (_option_leg(contract, 'buy'),), _units(budget, unit_cost), unit_cost,
                     OPTION_MULTIPLIER, _delta(contract))


def _first_expiry_members(members, today, settings):
    """Members at the earliest listed expiry at least `option_min_dte` days out."""
    earliest = today + timedelta(days=settings.option_min_dte)
    members = [c for c in members if (_expiry(c) or date.min) >= earliest]
    if not members:
        return []
    expiry = min(_expiry(c) for c in members)
    return [c for c in members if _expiry(c) == expiry]


def _at_the_money(decision, contracts, spot, today, settings):
    members = _first_expiry_members([c for c in contracts.values() if c.get('contract_type') == decision
                                     and _strike(c) is not None and (_quote(c, 'ask_price') or 0) > 0], today, settings)
    return min(members, key=lambda c: abs(_strike(c) - spot), default=None)


def _debit_spread(decision, production, contracts, spot, budget, settings, today):
    # Without production's contract (a shares_drift entry) the long leg is the at-the-money strike.
    long = (contracts.get(production['symbol'], production) if production.get('symbol')
            else _at_the_money(decision, contracts, spot, today, settings) or {})
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
    long_delta, short_delta = _delta(long), _delta(short)
    delta = long_delta - short_delta if long_delta is not None and short_delta is not None else None
    return OrderPlan('debit_spread', (_option_leg(long, 'buy'), _option_leg(short, 'sell')),
                     _units(budget, unit_cost), unit_cost, OPTION_MULTIPLIER, delta)


def _itm_single(decision, contracts, spot, budget, settings, today):
    """Deep in the money, at the first expiry `option_min_dte` days out that lists such a strike."""
    in_the_money = [c for c in contracts.values() if c.get('contract_type') == decision
                    and _strike(c) is not None and (_quote(c, 'ask_price') or 0) > 0
                    and (_strike(c) < spot if decision == 'call' else _strike(c) > spot)]
    deltas = {c['symbol']: safe_float((c.get('greeks') or {}).get('delta')) for c in in_the_money}
    if any(delta is not None for delta in deltas.values()):
        in_the_money = [c for c in in_the_money
                        if deltas[c['symbol']] is not None and abs(deltas[c['symbol']]) >= settings.itm_min_delta]
    in_the_money = _first_expiry_members(in_the_money, today, settings)
    if not in_the_money:
        return Skip('itm_single', 'no_itm_strike')
    return _buy_one_contract('itm_single', min(in_the_money, key=lambda c: abs(_strike(c) - spot)), budget)


def _longer_dte(decision, production, contracts, budget, settings):
    production_expiry = _expiry(production)
    if production_expiry is None:
        return Skip('longer_dte', 'no_production_expiry')
    # Strictly later even with no extension: production's own expiry would repeat its contract.
    earliest = production_expiry + timedelta(days=max(1, settings.longer_dte_extra_days))
    members = [c for c in contracts.values() if c.get('contract_type') == decision and _strike(c) is not None
               and (_quote(c, 'ask_price') or 0) > 0 and (_expiry(c) or date.min) >= earliest]
    if not members:
        return Skip('longer_dte', 'no_longer_expiry')
    expiry = min(_expiry(c) for c in members)
    # Production's strike, the nearest listed one winning and ties going further out of the money.
    target = _strike(production)
    further_otm = 1 if decision == 'call' else -1
    chosen = min((c for c in members if _expiry(c) == expiry),
                 key=lambda c: (abs(_strike(c) - target), -further_otm * _strike(c)))
    return _buy_one_contract('longer_dte', chosen, budget)


def _shares(decision, underlying, spot, budget):
    side = 'buy' if decision == 'call' else 'sell'
    return OrderPlan('shares', (OrderLeg(underlying, side, 'stock'),), _units(budget, spot), spot, 1,
                     1.0 if side == 'buy' else -1.0)


OPTION_STRATEGIES = ('debit_spread', 'itm_single', 'longer_dte')


def build_order(strategy, *, decision, underlying, production_contract, chains, spot, budget, settings, today=None):
    """`today` is the New York date the chain was fetched on; it dates the minimum DTE."""
    today = today or date.today()
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
    contracts = {c['symbol']: c for members in chains['buckets'].values() for c in members}
    if strategy == 'longer_dte':
        return _longer_dte(decision, production_contract, contracts, budget, settings)
    if strategy == 'debit_spread':
        return _debit_spread(decision, production_contract, contracts, spot, budget, settings, today)
    return _itm_single(decision, contracts, spot, budget, settings, today)
