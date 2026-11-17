"""Read-only drift report: the stock's 5-day residual from the system's first observation of each catalyst.

One row per catalyst, at its earliest candidate observation. Family and first-seen time come from the
candidate's catalyst state frozen at the cutoff; direction comes from the observation snapshot, so a
label added later never signs a move. A separate table groups by the current catalyst family and
direction, labelled as using later labels. `overall_all_observations` averages each catalyst over all
its directed observations, not only the first one.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from statistics import mean

from catalyst_models import timestamp
from db_common import get_connection
from services.research.research_contract import ResearchPolicy, SHARES_DRIFT_FAMILIES, SHARES_DRIFT_MAX_AGE_HOURS

AGE_BUCKETS = ('<1h', '1-24h', '>24h', 'unknown')
SIGNS = {'positive': 1, 'negative': -1}


def age_bucket(hours):
    if hours is None or hours < 0:
        return 'unknown'
    if hours < 1:
        return '<1h'
    if hours < 24:
        return '1-24h'
    return '>24h'


def _first_observations(conn, since):
    rows = conn.execute('''SELECT p.catalyst_id, p.observed_at, p.snapshot_json, p.forward_raw_residual_return,
            cc.catalyst_family, cc.state_json, c.catalyst_family AS current_family, c.direction AS current_direction
        FROM candidate_price_observations p
        LEFT JOIN candidate_catalysts cc ON cc.candidate_id=p.candidate_id AND cc.catalyst_id=p.catalyst_id
        LEFT JOIN catalysts c ON c.catalyst_id=p.catalyst_id
        ORDER BY julianday(p.observed_at), p.candidate_id''').fetchall()
    first, every = {}, {}
    for row in rows:
        first.setdefault(row['catalyst_id'], dict(row))
        direction = json.loads(row['snapshot_json'] or '{}').get('direction')
        if row['forward_raw_residual_return'] is not None and direction in SIGNS:
            every.setdefault(row['catalyst_id'], []).append(row['forward_raw_residual_return'] * SIGNS[direction])
    result = []
    for row in first.values():
        observed = timestamp(row['observed_at'])
        if since and observed < since:
            continue
        state = json.loads(row['state_json'] or '{}')
        seen = timestamp(state.get('first_seen_time'))
        result.append({'catalyst_id': row['catalyst_id'], 'day': observed.date().isoformat(),
                       'family': row['catalyst_family'] or 'unknown',
                       'direction': json.loads(row['snapshot_json'] or '{}').get('direction') or 'unknown',
                       'current_family': row['current_family'] or 'unknown',
                       'current_direction': row['current_direction'] or 'unknown',
                       'every_signed': every.get(row['catalyst_id']),
                       'age': age_bucket((observed - seen).total_seconds() / 3600 if seen else None),
                       'raw': row['forward_raw_residual_return']})
    return result


def _stats(rows, direction_key):
    signed = [row['raw'] * SIGNS[row[direction_key]] for row in rows if row[direction_key] in SIGNS]
    return {'catalysts': len(rows), 'days': len({row['day'] for row in rows}),
            'mean_signed_residual': mean(signed) if signed else None,
            'hit_rate': sum(value > 0 for value in signed) / len(signed) if signed else None,
            'mean_raw_residual': mean(row['raw'] for row in rows) if rows else None}


def _groups(rows, family_key, direction_key):
    def key(row):
        return row[family_key], row[direction_key], row['age']
    return [{'family': family, 'direction': direction, 'age': age,
             **_stats([row for row in rows if key(row) == (family, direction, age)], direction_key)}
            for family, direction, age in sorted({key(row) for row in rows})]


def _all_observations(rows):
    means = [(row['day'], mean(row['every_signed'])) for row in rows if row['every_signed']]
    return {'catalysts': len(means), 'days': len({day for day, _ in means}),
            'mean_signed_residual': mean(value for _, value in means) if means else None,
            'hit_rate': sum(value > 0 for _, value in means) / len(means) if means else None}


def drift_report_from_connection(conn, *, days=0, now=None):
    now = now or datetime.now(timezone.utc)
    observations = _first_observations(conn, now - timedelta(days=days) if days else None)
    labelled = [row for row in observations if row['raw'] is not None]
    return {
        'as_of': now.isoformat(), 'days': days,
        'definitions': {
            'unit': 'one catalyst, at its earliest candidate observation',
            'residual': '5-day residual return (net of SPY and sector betas) from the observation time',
            'signed': 'residual times +1 for a positive and -1 for a negative point-in-time direction; '
                      'unknown or mixed directions are not signed',
            'age': 'observation time minus the catalyst first_seen_time frozen with the candidate',
            'overall_all_observations': 'each catalyst averaged over all its observations with a point-in-time '
                                        'direction, keyed to its first observation day',
        },
        'overall': _stats([row for row in labelled if row['direction'] in SIGNS], 'direction'),
        'overall_all_observations': _all_observations(observations),
        'groups': _groups(labelled, 'family', 'direction'),
        'uses_later_labels': _groups(labelled, 'current_family', 'current_direction'),
        'coverage': {'catalysts': len(observations), 'labelled_catalysts': len(labelled),
                     'unlabelled_catalysts': len(observations) - len(labelled)},
    }


def drift_report(*, days=0, db_path=None, now=None):
    with closing(get_connection(db_path)) as conn:
        return drift_report_from_connection(conn, days=days, now=now)


def _trade_stats(rows):
    raw = [row['raw'] for row in rows]
    residual = [row['residual'] for row in rows if row['residual'] is not None]
    return {'catalysts': len(rows), 'days': len({row['day'] for row in rows}),
            'mean_raw_return': mean(raw) if raw else None,
            'raw_hit_rate': sum(value > 0 for value in raw) / len(raw) if raw else None,
            'residual_catalysts': len(residual),
            'mean_residual_return': mean(residual) if residual else None,
            'residual_hit_rate': sum(value > 0 for value in residual) / len(residual) if residual else None}


def shares_drift_report_from_connection(conn, run_id):
    """A research run's shares_drift trades, one per catalyst at its first entry, as the live followers enter."""
    rows = conn.execute('''SELECT p.information_cutoff_time, p.result_json, cc.catalyst_id, cc.state_json
        FROM research_shadow_positions p
        LEFT JOIN candidate_catalysts cc ON cc.candidate_id=p.candidate_id AND cc.is_primary=1
        WHERE p.run_id=? AND p.strategy='shares_drift' AND p.status='complete'
        ORDER BY julianday(p.information_cutoff_time), p.candidate_id''', (run_id,)).fetchall()
    first = {}
    for row in rows:
        result, state = json.loads(row['result_json']), json.loads(row['state_json'] or '{}')
        capital = result['capital']
        first.setdefault(row['catalyst_id'] or row['information_cutoff_time'], {
            'day': row['information_cutoff_time'][:10], 'family': state.get('catalyst_family') or 'unknown',
            'direction': state.get('direction') or 'unknown', 'raw': result['hypothetical_pnl'] / capital,
            'residual': None if result.get('residual_pnl') is None else result['residual_pnl'] / capital})
    trades = list(first.values())
    return {'run_id': run_id, 'trades': len(rows),
            'definitions': {
                'unit': 'one catalyst, at its first complete shares_drift trade; repeat candidates are dropped',
                'returns': 'per $1 of notional after costs; residual is net of SPY and the sector over the same '
                           'entry and exit bars as the trade, and unmeasured when they cannot price those bars'},
            'overall': _trade_stats(trades), 'groups': _family_groups(trades)}


def _family_groups(trades):
    keys = sorted({(row['family'], row['direction']) for row in trades})
    return [{'family': family, 'direction': way,
             **_trade_stats([row for row in trades if (row['family'], row['direction']) == (family, way)])}
            for family, way in keys]


def shares_drift_report(run_id, *, db_path=None):
    with closing(get_connection(db_path)) as conn:
        return shares_drift_report_from_connection(conn, run_id)


FEED_WINDOW = timedelta(hours=SHARES_DRIFT_MAX_AGE_HOURS)
# The live labeler answers in about 15 minutes to 2 hours; 2h stands in for labels made after the fact.
ASSUMED_LABEL_LAG = timedelta(hours=2)


def _feed_signals(conn, since):
    """Each directed drift-family catalyst with when its direction was known, or why it never qualifies."""
    rows = conn.execute(f'''SELECT c.catalyst_id, c.ticker, c.catalyst_family, c.direction, c.first_public_time,
            c.first_seen_time, json_extract(c.state_json, '$.classification_source') AS source,
            (SELECT l.labeled_at FROM catalyst_model_labels l WHERE l.catalyst_id=c.catalyst_id
             AND l.direction=c.direction AND l.confidence!='low' ORDER BY julianday(l.labeled_at) LIMIT 1) AS labeled_at,
            (SELECT k.created_at FROM candidate_catalysts cc JOIN candidates k ON k.candidate_id=cc.candidate_id
             WHERE cc.catalyst_id=c.catalyst_id ORDER BY julianday(k.created_at) LIMIT 1) AS candidate_at
        FROM catalysts c
        WHERE c.catalyst_family IN ({','.join('?' * len(SHARES_DRIFT_FAMILIES))})
        AND c.direction IN ('positive', 'negative') AND c.ticker IS NOT NULL AND c.first_seen_time IS NOT NULL''',
                        SHARES_DRIFT_FAMILIES).fetchall()
    signals = []
    for row in rows:
        seen, published = timestamp(row['first_seen_time']), timestamp(row['first_public_time'])
        if since and seen < since:
            continue
        signal = {'catalyst_id': row['catalyst_id'], 'ticker': row['ticker'], 'family': row['catalyst_family'],
                  'direction': row['direction'], 'day': seen.date().isoformat(), 'known_at': None, 'excluded': None}
        candidate = timestamp(row['candidate_at'])
        signal['had_candidate'] = bool(candidate and candidate - seen < FEED_WINDOW)
        if published is None or seen - published >= FEED_WINDOW:
            signal['excluded'] = 'stale_publication'
        elif row['source'] == 'rules':
            signal['known_at'] = seen
        else:
            labeled = timestamp(row['labeled_at'])
            if labeled is not None and labeled - seen < FEED_WINDOW:
                signal['known_at'] = max(labeled, seen)
            else:
                signal['excluded'], signal['assumed_at'] = 'no_in_time_label', seen + ASSUMED_LABEL_LAG
        signals.append(signal)
    return signals


def _replay(conn, signals, key, as_of, price, db_path, coverage):
    """Price signals in time order, one open position per ticker; returns the complete trades."""
    run, reads, open_until, trades = {'policy': ResearchPolicy()}, {}, {}, []
    for signal in sorted(signals, key=lambda row: (row[key], row['catalyst_id'])):
        at = signal[key]
        if at < open_until.get(signal['ticker'], at):
            coverage['ticker_open'] += 1
            continue
        result = price(conn, {'information_cutoff_time': at.isoformat(), 'symbol': signal['ticker']},
                       'call' if signal['direction'] == 'positive' else 'put', run, as_of.isoformat(), reads, db_path)
        if result['status'] != 'complete':
            coverage[result['status']] = coverage.get(result['status'], 0) + 1
            continue
        open_until[signal['ticker']] = timestamp(result['hypothetical_exit']['at'])
        capital, residual = result['capital'], result.get('residual_pnl')
        if residual is None:
            coverage['window_mismatch'] += 1
        trades.append({**signal, 'raw': result['hypothetical_pnl'] / capital,
                       'residual': None if residual is None else residual / capital})
    return trades


def _feed_section(trades, coverage):
    return {'overall': _trade_stats(trades), 'coverage': coverage, 'groups': _family_groups(trades),
            'by_candidate': [{'had_candidate': flag, **_trade_stats([row for row in trades if row['had_candidate'] == flag])}
                             for flag in (True, False) if any(row['had_candidate'] == flag for row in trades)]}


def catalyst_feed_report_from_connection(conn, *, as_of, since=None, db_path=None, price=None):
    """The shares_drift rule replayed on catalysts as they were labelled, instead of on candidates (read-only)."""
    if price is None:
        from market_db_modules.research_runs import stock_position as price
    signals = _feed_signals(conn, since)
    base = {'ticker_open': 0, 'pending': 0, 'missing_stock_prices': 0, 'window_mismatch': 0}
    timely = {**base, 'stale_publication': sum(row['excluded'] == 'stale_publication' for row in signals),
              'no_in_time_label': sum(row['excluded'] == 'no_in_time_label' for row in signals)}
    late = [row for row in signals if row['excluded'] == 'no_in_time_label']
    assumed = dict(base)
    return {
        'as_of': as_of.isoformat(), 'since': since.isoformat() if since else None,
        'definitions': {
            'signal': 'an earnings or analyst catalyst with a positive or negative direction, published under 24h '
                      'before the system first saw it; long for positive, short for negative',
            'point_in_time': "entry from first_seen_time for rules-only directions, else from the first non-low "
                             "model label agreeing with the direction, made under 24h after first seen",
            'assumed_label_lag': 'model-directed catalysts with no such label (the labeler started 2026-10-29), '
                                 'entered at first_seen_time + 2h; their own replay, never mixed with point_in_time',
            'ticker_key': 'one open position per ticker: later signals on it are skipped until it exits, whatever '
                          'their direction',
            'trade': 'research_runs.stock_position: fixed-notional shares from the first completed close at or after '
                     'entry (1h bars, else 1d) held 5 days, after costs; residual net of SPY and the sector over '
                     'the same bars',
            'had_candidate': 'the catalyst also got a candidate under 24h after first seen, as the live feed needs',
            'limits': 'a rules-only direction is the current one, which can shift as articles join the catalyst',
        },
        'point_in_time': _feed_section(_replay(conn, [row for row in signals if row['known_at']], 'known_at', as_of,
                                               price, db_path, timely), timely),
        'assumed_label_lag': _feed_section(_replay(conn, late, 'assumed_at', as_of, price, db_path, assumed), assumed),
    }


def catalyst_feed_report(*, days=0, db_path=None, now=None, price=None):
    now = now or datetime.now(timezone.utc)
    with closing(get_connection(db_path)) as conn:
        return catalyst_feed_report_from_connection(conn, as_of=now, since=now - timedelta(days=days) if days else None,
                                                    db_path=db_path, price=price)
