"""Quote-based counterfactuals. A quoted fill is an assumption, never a broker fill."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from services.research.research_contract import utc
from services.core.numbers import finite_float
from services.core.trade_sides import TRADE_SIDES


def valid_quote(quote):
    bid, ask = finite_float(quote.get('bid_price')), finite_float(quote.get('ask_price'))
    return bid is not None and ask is not None and 0 < ask and 0 <= bid <= ask


NEW_YORK = ZoneInfo('America/New_York')


def session_close(local_date):
    return datetime.combine(local_date, time(16), NEW_YORK)


def session_dates(market_sessions):
    """Observed sessions as New York dates, which is the basis daily marks are keyed on."""
    return {utc(session).astimezone(NEW_YORK).date() for session in market_sessions or ()}


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
    price = finite_float(entry['ask_price'])
    capital = price * 100 + policy.fee_per_contract_side
    base.update(hypothetical_entry={'at': cutoff, 'quote_at': entry['quote_at'], 'price': price,
                                  'quote_id': entry.get('id'), 'provider': entry['provider']}, capital=capital)
    target = start + timedelta(days=policy.holding_days)
    try:
        expiry = datetime.combine(utc(contract['expiration_date']).date(), time(16), NEW_YORK) if contract.get('expiration_date') else None
    except ValueError:
        return {**base, 'status': 'invalid_contract_expiry'}
    if not expiry or expiry < target:
        return {**base, 'status': 'expiry_before_horizon'}
    sessions = session_dates(market_sessions)
    daily = {}
    for quote in quotes:
        at = utc(quote['quote_at'])
        if at > start:
            daily[at.astimezone(NEW_YORK).date()] = quote
    peak, previous, previous_date = price, start, start.astimezone(NEW_YORK).date()
    for local_date, quote in daily.items():
        at, bid = utc(quote['quote_at']), finite_float(quote['bid_price'])
        # A day's last known quote is only that day's mark once the session can no longer
        # move it. Mid-session, acting on it would freeze an outcome the rest of the day
        # could still erase, so wait rather than finalize. Later days are open too.
        if end < session_close(local_date):
            return {**base, 'status': 'pending'}
        if sessions and local_date not in sessions:
            # Session evidence that has not arrived yet can still confirm this day. Once
            # observed coverage runs past it, its absence is permanent: finalize as
            # unevaluable instead of re-collecting a day that can never be confirmed.
            return {**base, 'status': 'missing_daily_marks' if max(sessions) > local_date else 'pending'}
        if any(previous_date < session < local_date for session in sessions):
            return {**base, 'status': 'missing_daily_marks'}
        if (at - previous).total_seconds() > policy.maximum_mark_gap_days * 86400:
            return {**base, 'status': 'missing_daily_marks'}
        if at > expiry:
            return {**base, 'status': 'missing_expiry_quote'}
        previous, previous_date, peak = at, local_date, max(peak, bid)
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
                    # Every mark up to this exit was matched against an observed session above.
                    'verified_daily_marks': bool(sessions),
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


MOVE_BASIS = 'underlying_residual_move'
HARD_BLOCK_BASIS = 'hard_block_precision'
# Share of direction-bearing verdicts that must have a resolved forward move; matches forward acceptance.
MINIMUM_MOVE_COVERAGE = 0.9
SIDES = {**TRADE_SIDES, 'positive': 1, 'negative': -1}


def proposed_side(features):
    """+1/-1 for the trade a candidate proposed: the manager's call/put, else the catalyst's direction."""
    return SIDES.get(features.get('manager_direction') if features.get('manager_direction') in {'call', 'put'}
                     else features.get('catalyst_direction'))


def move_gate_value(rejected_moves):
    """A gate's value on the side-signed moves it rejected: losses avoided minus profits sacrificed."""
    lost = -sum(move for move in rejected_moves if move < 0)
    sacrificed = sum(move for move in rejected_moves if move > 0)
    return {'losses_avoided': lost, 'profits_sacrificed': sacrificed, 'gate_value': lost - sacrificed}


def evaluate_gate_on_hard_blocks(rows, stage):
    """Score a pre-check by how often its rejections were later hard-blocked anyway.

    Each row's `outcome` is 'blocked' (premium cap or no eligible contract), 'fits' (an order went out)
    or None. Only a rejection where the manager proposed a call or put can reach either outcome, so
    coverage is the share of those with a known outcome; a pick REGIME or CONTINUATION stopped first
    counts as missing. gate_value is the precision of the rejections whose outcome is known.
    """
    verdicts = [row for row in rows if row['gates'].get(stage) in (True, False)]
    rejected = [row for row in verdicts if row['gates'][stage] is False]
    applicable = [row for row in rejected if row['features'].get('manager_direction') in {'call', 'put'}]
    known = [row for row in applicable if row.get('outcome') in ('blocked', 'fits')]
    blocked = sum(row['outcome'] == 'blocked' for row in known)
    coverage = len(known) / len(applicable) if applicable else 0.0
    return {'stage': stage, 'basis': HARD_BLOCK_BASIS, 'n_seen': len(verdicts),
            'n_unknown_verdicts': sum(stage in row['gates'] and row['gates'][stage] is None for row in rows),
            'n_passed': len(verdicts) - len(rejected), 'n_rejected': len(rejected),
            'n_not_applicable': len(rejected) - len(applicable),
            'n_evaluable': len(known), 'n_blocked': blocked, 'n_missing_outcomes': len(applicable) - len(known),
            'n_passed_later_blocked': sum(row['gates'][stage] is True and row.get('outcome') == 'blocked'
                                          for row in verdicts),
            'gate_value': blocked / len(known) if known else 0.0, 'coverage': coverage,
            'coverage_complete': bool(applicable) and coverage >= MINIMUM_MOVE_COVERAGE,
            'denominator_basis': 'rejected manager trades with a known outcome; gate value is the share later hard-blocked'}


def evaluate_gate_on_moves(rows, stage):
    """Score one gate's verdicts on the underlying's forward residual move in the proposed direction.

    No priced trade is needed, so candidates the manager never reached or passed on still count.
    A rejected candidate whose move went against its side is a loss avoided; with it, a profit sacrificed.
    """
    verdicts = [row for row in rows if row['gates'].get(stage) in (True, False)]
    applicable = [row for row in verdicts if proposed_side(row['features'])]
    scored = [(row['gates'][stage], row['residual_return'] * proposed_side(row['features']))
              for row in applicable if row.get('residual_return') is not None]
    passed = [move for allowed, move in scored if allowed]
    rejected = [move for allowed, move in scored if not allowed]
    coverage = len(scored) / len(applicable) if applicable else 0.0
    return {'stage': stage, 'basis': MOVE_BASIS,
            'n_seen': len(applicable), 'n_no_direction': len(verdicts) - len(applicable),
            'n_unknown_verdicts': sum(stage in row['gates'] and row['gates'][stage] is None for row in rows),
            'n_passed': sum(row['gates'][stage] for row in applicable),
            'n_rejected': sum(not row['gates'][stage] for row in applicable),
            'n_evaluable': len(scored), 'n_missing_outcomes': len(applicable) - len(scored),
            'mean_move_passed': sum(passed) / len(passed) if passed else None,
            'mean_move_rejected': sum(rejected) / len(rejected) if rejected else None,
            **move_gate_value(rejected),
            'coverage': coverage, 'coverage_complete': bool(applicable) and coverage >= MINIMUM_MOVE_COVERAGE,
            'denominator_basis': '5-day residual return signed by the proposed side; gate value in summed return units'}
