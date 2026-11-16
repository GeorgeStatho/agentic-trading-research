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
    keys = sorted({(row['family'], row['direction']) for row in trades})
    return {'run_id': run_id, 'trades': len(rows),
            'definitions': {
                'unit': 'one catalyst, at its first complete shares_drift trade; repeat candidates are dropped',
                'returns': 'per $1 of notional after costs; residual is net of SPY and the sector from the cutoff'},
            'overall': _trade_stats(trades),
            'groups': [{'family': family, 'direction': way,
                        **_trade_stats([row for row in trades if (row['family'], row['direction']) == (family, way)])}
                       for family, way in keys]}


def shares_drift_report(run_id, *, db_path=None):
    with closing(get_connection(db_path)) as conn:
        return shares_drift_report_from_connection(conn, run_id)
