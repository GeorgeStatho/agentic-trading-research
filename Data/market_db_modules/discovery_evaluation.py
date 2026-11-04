"""Discovery evaluated on the union of admitted events, recording which route admitted each one.

Every event (catalyst) seen in the window gets a status per route: admitted, deliberately not
admitted (with its reason), or never evaluated. Admitted events are scored with the catalyst's
own stored price response: the residual move at each horizon, signed by the catalyst direction.
No model is called and no option is simulated.
"""
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from statistics import mean, median

from db_common import get_connection
from services.research.research_contract import utc
from services.research.research_promotion import research_only_candidate
from .bootstrap import initialize_database

ROUTES = ('production', 'discovery')
HORIZONS = (1, 3, 5, 10)
SIGN = {'positive': 1, 'negative': -1}
# Pre-registered 2026-11-04, before any results were read: the bar for setting DISCOVERY_MODE=enforce.
# Amended the same day, still before any discovery-only outcome resolved, to require company news.
PROMOTION_CRITERIA = {'horizon': '5d', 'min_shadow_days': 14, 'min_discovery_only_events': 40,
                      'discovery_only_requires_company_news_days': 5,
                      'discovery_only_at_least_ranking': ['mean_absolute_residual_return', 'hit_rate']}


def _production_route(conn, since, until):
    """The existing sector/opportunist route: did a candidate for the event reach the strategist?

    Candidates DISCOVERY_MODE=enforce routed into production belong to the discovery route, not the ranking.
    """
    events = defaultdict(list)
    for row in conn.execute('''SELECT c.candidate_id, c.created_at, cc.catalyst_id, cc.state_json FROM candidates c
            JOIN candidate_catalysts cc ON cc.candidate_id=c.candidate_id AND cc.is_primary=1
            WHERE julianday(c.created_at) BETWEEN julianday(?) AND julianday(?)
            AND NOT EXISTS (SELECT 1 FROM discovery_production_routes r WHERE r.candidate_id=c.candidate_id)''',
                            (since, until)):
        if research_only_candidate(row['candidate_id']):
            continue
        stages = {stage[0] for stage in conn.execute(
            'SELECT stage FROM candidate_snapshots WHERE candidate_id=?', (row['candidate_id'],))}
        rejection = conn.execute('''SELECT reason_code FROM candidate_rejections WHERE candidate_id=? AND shadow=0
            AND stage IN ('SUPPORT', 'CYCLE') ORDER BY id LIMIT 1''', (row['candidate_id'],)).fetchone()
        if 'pre_strategist' in stages:
            status = ('admitted', None)
        elif 'admission_blocked' in stages:
            status = ('not_admitted', 'catalyst_admission_blocked')
        elif rejection:
            status = ('not_admitted', rejection['reason_code'])
        else:
            status = ('not_evaluated', 'unfinished')
        events[row['catalyst_id']].append({'status': status[0], 'reason': status[1], 'at': row['created_at'],
                                           'state': json.loads(row['state_json'])})
    return events


def _discovery_route(conn, since, until):
    events = defaultdict(list)
    for row in conn.execute('''SELECT q.queue_id, q.catalyst_id, q.status, q.completion_reason,
            json_extract(q.record_json, '$.created_at') AS created_at, d.admission_status, d.reason,
            d.created_at AS admitted_at, d.payload_json FROM discovery_queue q
            LEFT JOIN discovery_shadow_candidates d ON d.queue_id=q.queue_id
            WHERE q.purpose='entry' AND julianday(json_extract(q.record_json, '$.created_at'))
            BETWEEN julianday(?) AND julianday(?)''', (since, until)):
        if row['admission_status'] == 'ready':
            route = (json.loads(row['payload_json']).get('admission') or {}).get('route')
            status = ('admitted', route)
        elif row['admission_status'] == 'deferred':
            status = ('not_admitted', row['reason'])
        else:
            status = ('not_evaluated', row['completion_reason'] or row['status'])
        events[row['catalyst_id']].append({'status': status[0], 'reason': status[1],
                                           'at': row['admitted_at'] or row['created_at']})
    return events


def _route_status(entries):
    """An event counts as admitted if any of its items was; otherwise the latest reason stands."""
    if not entries:
        return {'status': 'not_evaluated', 'reason': 'no_candidate_or_queue_item', 'admitted_at': None}
    admitted = sorted((entry for entry in entries if entry['status'] == 'admitted'), key=lambda entry: entry['at'])
    if admitted:
        return {'status': 'admitted', 'reason': admitted[0]['reason'], 'admitted_at': admitted[0]['at']}
    latest = max(entries, key=lambda entry: entry['at'] or '')
    return {'status': latest['status'], 'reason': latest['reason'], 'admitted_at': None}


def _responses(conn, catalyst_ids, as_of):
    result = defaultdict(dict)
    for catalyst_id in catalyst_ids:
        event = conn.execute('SELECT direction FROM catalyst_response_events WHERE catalyst_id=?', (catalyst_id,)).fetchone()
        for row in conn.execute('''SELECT horizon_days, status, residual_return FROM catalyst_responses
                WHERE catalyst_id=? AND julianday(recorded_at)<=julianday(?)''', (catalyst_id, as_of)):
            sign = SIGN.get(event['direction']) if event else None
            complete = row['status'] == 'complete' and row['residual_return'] is not None
            result[catalyst_id][int(row['horizon_days'])] = {
                'status': row['status'] if event else 'not_registered',
                'signed_residual_return': row['residual_return'] * sign if complete and sign else None,
                'absolute_residual_return': abs(row['residual_return']) if complete else None}
    return result


def _has_company_news(conn, symbol, admitted_at):
    """The strategist can only decide an event whose company has classified news around its admission."""
    days = PROMOTION_CRITERIA['discovery_only_requires_company_news_days']
    return conn.execute('''SELECT 1 FROM company_opportunist_impacts i JOIN companies c ON c.id=i.company_id
        JOIN news_articles a ON a.id=i.article_id WHERE UPPER(c.symbol)=UPPER(?)
        AND julianday(a.published_at) BETWEEN julianday(?) - ? AND julianday(?) + 1 LIMIT 1''',
                        (symbol, admitted_at, days, admitted_at)).fetchone() is not None


def _group_outcomes(rows, responses):
    summary = {}
    for horizon in HORIZONS:
        signed = [responses[row['catalyst_id']].get(horizon, {}).get('signed_residual_return') for row in rows]
        absolute = [responses[row['catalyst_id']].get(horizon, {}).get('absolute_residual_return') for row in rows]
        signed = [value for value in signed if value is not None]
        absolute = [value for value in absolute if value is not None]
        summary[f'{horizon}d'] = {'events': len(rows), 'resolved': len(signed),
                                  'mean_signed_residual_return': mean(signed) if signed else None,
                                  'hit_rate': sum(value > 0 for value in signed) / len(signed) if signed else None,
                                  'mean_absolute_residual_return': mean(absolute) if absolute else None}
    return summary


def promotion_check(discovery_only, ranking_sourced, *, first_admitted_at, until):
    """Discovery-only admitted events must match ranking-sourced ones on enough shadow data."""
    criteria = PROMOTION_CRITERIA
    days = (utc(until) - utc(first_admitted_at)).total_seconds() / 86400 if first_admitted_at else 0
    failures = []
    if days < criteria['min_shadow_days']:
        failures.append('shadow_days_below_minimum')
    if discovery_only['resolved'] < criteria['min_discovery_only_events']:
        failures.append('discovery_only_events_below_minimum')
    for metric in criteria['discovery_only_at_least_ranking']:
        if discovery_only[metric] is None or ranking_sourced[metric] is None:
            failures.append(f'{metric}_unresolved')
        elif discovery_only[metric] < ranking_sourced[metric]:
            failures.append(f'{metric}_below_ranking')
    return {'passed': not failures, 'failures': failures, 'criteria': criteria, 'shadow_days': days,
            'discovery_only': discovery_only, 'ranking_sourced': ranking_sourced}


def discovery_route_ledger(*, days=30, as_of=None, db_path=None):
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn:
        return route_ledger(conn, days=days, as_of=as_of)


def route_ledger(conn, *, days=30, as_of=None):
    until = utc(as_of or datetime.now(timezone.utc))
    since = (until - timedelta(days=days)).isoformat()
    routes = {'production': _production_route(conn, since, until.isoformat()),
              'discovery': _discovery_route(conn, since, until.isoformat())}
    catalyst_ids = sorted(set(routes['production']) | set(routes['discovery']))
    states = {row['catalyst_id']: json.loads(row['state_json']) for row in conn.execute(
        f'SELECT catalyst_id, state_json FROM catalysts WHERE catalyst_id IN ({",".join("?" for _ in catalyst_ids)})',
        catalyst_ids)} if catalyst_ids else {}
    responses = _responses(conn, catalyst_ids, until.isoformat())
    ledger = []
    for catalyst_id in catalyst_ids:
        statuses = {route: _route_status(routes[route].get(catalyst_id, [])) for route in ROUTES}
        state = states.get(catalyst_id, {})
        ledger.append({'catalyst_id': catalyst_id, 'ticker': state.get('ticker'),
                       'catalyst_family': state.get('catalyst_family'), 'direction': state.get('direction'),
                       'first_public_time': state.get('first_public_time'), 'routes': statuses})
    admitted = [row for row in ledger if any(route['status'] == 'admitted' for route in row['routes'].values())]
    groups = defaultdict(list)
    for row in admitted:
        admitted_by = tuple(route for route in ROUTES if row['routes'][route]['status'] == 'admitted')
        groups['both' if len(admitted_by) == 2 else admitted_by[0] + '_only'].append(row)
    route_counts = {route: defaultdict(int) for route in ROUTES}
    reasons = {route: defaultdict(int) for route in ROUTES}
    latency = {route: [] for route in ROUTES}
    for row in ledger:
        for route in ROUTES:
            entry = row['routes'][route]
            route_counts[route][entry['status']] += 1
            if entry['status'] != 'admitted':
                reasons[route][f"{entry['status']}:{entry['reason']}"] += 1
            elif row['first_public_time'] and entry['admitted_at']:
                latency[route].append((utc(entry['admitted_at']) - utc(row['first_public_time'])).total_seconds())
    horizon = PROMOTION_CRITERIA['horizon']
    discovery_admissions = [row['routes']['discovery']['admitted_at'] for row in admitted
                            if row['routes']['discovery']['status'] == 'admitted']
    with_news = [row for row in groups['discovery_only']
                 if _has_company_news(conn, row['ticker'], row['routes']['discovery']['admitted_at'])]
    check = promotion_check(_group_outcomes(with_news, responses)[horizon],
                            _group_outcomes(groups['production_only'] + groups['both'], responses)[horizon],
                            first_admitted_at=min(discovery_admissions, default=None), until=until)
    check['discovery_only_without_news'] = len(groups['discovery_only']) - len(with_news)
    return {'window': {'since': since, 'until': until.isoformat()}, 'events_seen': len(ledger),
            'admitted_union': len(admitted),
            'route_status_counts': {route: dict(counts) for route, counts in route_counts.items()},
            'non_admission_reasons': {route: dict(sorted(counts.items())) for route, counts in reasons.items()},
            'admission_latency_seconds_median': {route: median(values) if values else None
                                                 for route, values in latency.items()},
            'outcomes_by_admission': {group: _group_outcomes(rows, responses) for group, rows in sorted(groups.items())},
            'promotion_check': check,
            'admitted_events': admitted,
            'basis': 'stored catalyst residual responses signed by catalyst direction; events whose catalyst was '
                     'never registered for response collection stay unresolved, never zero'}
