"""Quote-based counterfactuals. A quoted fill is an assumption, never a broker fill."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from services.research_contract import number, utc


def valid_quote(quote):
    bid, ask = number(quote.get('bid_price')), number(quote.get('ask_price'))
    return bid is not None and ask is not None and 0 < ask and 0 <= bid <= ask


def simulate_option(contract, quotes, *, cutoff, as_of, policy, market_sessions=None):
    base = {'information_cutoff_time': cutoff, 'outcome_cutoff_time': as_of,
            'hypothetical_entry': None, 'hypothetical_exit': None, 'hypothetical_pnl': None,
            'deployed_dollar_days': None, 'fill_assumption': 'one contract at ask; liquidation at bid',
            'exit_policy': policy.version, 'marks': [], 'verified_daily_marks': False}
    if not contract:
        return {**base, 'status': 'missing_contract'}
    base['option_symbol'] = contract.get('symbol') or contract.get('option_symbol')
    start, end = utc(cutoff), utc(as_of)
    # Do not combine vendors into a synthetic bid/ask history.
    quotes = sorted((q for q in quotes if valid_quote(q) and utc(q['quote_at']) <= end), key=lambda q: utc(q['quote_at']))
    entries = [q for q in quotes if 0 <= (start - utc(q['quote_at'])).total_seconds() <= policy.entry_quote_age_seconds]
    if not entries:
        return {**base, 'status': 'missing_entry_quote'}
    entry = entries[-1]
    quotes = [q for q in quotes if q['provider'] == entry['provider']]
    price = number(entry['ask_price'])
    capital = price * 100 + policy.fee_per_contract_side
    base.update(hypothetical_entry={'at': cutoff, 'quote_at': entry['quote_at'], 'price': price,
                                  'quote_id': entry.get('id'), 'provider': entry['provider']}, capital=capital)
    target = start + timedelta(days=policy.holding_days)
    try:
        expiry = datetime.combine(utc(contract['expiration_date']).date(), time(16), ZoneInfo('America/New_York')) if contract.get('expiration_date') else None
    except ValueError:
        return {**base, 'status': 'invalid_contract_expiry'}
    if not expiry or expiry < target:
        return {**base, 'status': 'expiry_before_horizon'}
    daily = {}
    for quote in quotes:
        at = utc(quote['quote_at'])
        if at > start:
            daily[at.astimezone(ZoneInfo('America/New_York')).date()] = quote
    peak, previous = price, start
    for quote in daily.values():
        at, bid = utc(quote['quote_at']), number(quote['bid_price'])
        if market_sessions and any(previous.date() < utc(session).date() < at.date() for session in market_sessions):
            return {**base, 'status': 'missing_daily_marks'}
        if (at - previous).total_seconds() > policy.maximum_mark_gap_days * 86400:
            return {**base, 'status': 'missing_daily_marks'}
        if at > expiry:
            return {**base, 'status': 'missing_expiry_quote'}
        previous, peak = at, max(peak, bid)
        reasons = []
        if bid <= price * (1 - policy.stop_loss):
            reasons.append('risk_stop')
        if at >= target:
            reasons.append('catalyst_expired')
        if bid >= price * (1 + policy.take_profit):
            reasons.append('take_profit')
        if peak > price and bid <= peak * (1 - policy.trailing_drawdown):
            reasons.append('trailing_stop')
        base['marks'].append({'quote_id': quote.get('id'), 'at': quote['quote_at'], 'bid': bid,
                              'coactive_exit_reasons': reasons})
        if reasons:
            days = (at - start).total_seconds() / 86400
            return {**base, 'status': 'complete',
                    'verified_daily_marks': bool(market_sessions) and any(utc(session).date() == at.date() for session in market_sessions),
                    'hypothetical_exit': {'at': quote['quote_at'], 'price': bid, 'reason': reasons[0],
                                          'quote_id': quote.get('id')},
                    'hypothetical_pnl': (bid - price) * 100 - 2 * policy.fee_per_contract_side,
                    'deployed_dollar_days': capital * days}
    status = 'pending' if end < target else 'missing_exit_quote'
    return {**base, 'status': status}


def expectancy(pnl, trades, candidates, dollar_days):
    return {'per_executed_trade': pnl / trades if trades else None,
            'per_candidate_observed': pnl / candidates if candidates else None,
            'per_deployed_dollar_day': pnl / dollar_days if dollar_days else None}


def evaluate_gate(rows, stage):
    unknown = sum(stage in row['gates'] and row['gates'][stage] is None for row in rows)
    seen = [row for row in rows if row['gates'].get(stage) in (True, False)]
    passed = [row for row in seen if row['gates'][stage]]
    rejected = [row for row in seen if not row['gates'][stage]]
    completed = [row for row in seen if row['result']['status'] == 'complete']
    passed_done = [row for row in completed if row['gates'][stage]]
    rejected_done = [row for row in completed if not row['gates'][stage]]
    pnl = lambda items: sum(row['result']['hypothetical_pnl'] for row in items)
    winners = [row for row in rejected_done if row['result']['hypothetical_pnl'] > 0]
    losers = [row for row in rejected_done if row['result']['hypothetical_pnl'] < 0]
    lost, sacrificed = -pnl(losers), pnl(winners)
    dollars = sum(row['result']['deployed_dollar_days'] for row in passed_done)
    return {'stage': stage, 'n_seen': len(seen), 'n_passed': len(passed), 'n_rejected': len(rejected),
            'n_evaluable': len(completed), 'n_missing_outcomes': len(seen) - len(completed), 'n_unknown_verdicts': unknown,
            'passed_winners': sum(row['result']['hypothetical_pnl'] > 0 for row in passed_done),
            'passed_losers': sum(row['result']['hypothetical_pnl'] < 0 for row in passed_done),
            'rejected_winners': len(winners), 'rejected_losers': len(losers),
            'rejected_flat': len(rejected_done) - len(winners) - len(losers),
            'pnl_passed': pnl(passed_done), 'hypothetical_pnl_rejected': pnl(rejected_done),
            'losses_avoided': lost, 'profits_sacrificed': sacrificed, 'gate_value': lost - sacrificed,
            'gate_efficiency': lost / max(len(winners), 1),
            'incremental_expectancy_per_candidate': (lost - sacrificed) / len(seen) if seen else None,
            'expectancy': expectancy(pnl(passed_done), len(passed_done), len(seen), dollars),
            'denominator_basis': 'hypothetical passed trades; all gate-observed candidates; premium dollar-days',
            'coverage_complete': bool(seen) and len(completed) == len(seen) and unknown == 0}
