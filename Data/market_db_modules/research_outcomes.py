"""Stock-level outcomes of shadow research theses, beside the research route's cost and latency.

A thesis is scored on the stock's residual (market- and sector-adjusted) move in its stated
direction, from when the thesis was written to its stated horizon, snapped down to a stored
response horizon. Moves before the thesis existed are never credited. No option is simulated.
"""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from statistics import mean, median

from db_common import DB_PATH, get_connection
from services.price_response import measure_response
from services.pricing_policy import RESPONSE_HORIZONS
from services.research_contract import canonical, utc
from .bootstrap import initialize_database
from .pricing_research import load_histories, sector_symbol

OUTCOME_VERSION = 'research-thesis-outcome-v1'
ROUTE = 'research_shadow'
SIGN = {'up': 1, 'down': -1}
BATCH = 100


def outcome_horizon(thesis):
    longest = ((thesis or {}).get('horizon') or {}).get('maximum_days')
    usable = [horizon for horizon in RESPONSE_HORIZONS if longest and horizon <= longest]
    return max(usable) if usable else min(RESPONSE_HORIZONS)


def _theses(conn, *, since=None, unresolved_only=False, limit=None):
    query = '''SELECT c.record_json, c.created_at, co.symbol, s.record_json AS session_json, ca.state_json AS catalyst_json,
        o.status AS outcome_status FROM evidence_thesis_checks c
        JOIN companies co ON co.id=c.company_id
        LEFT JOIN research_sessions s ON s.session_id=c.research_session_id
        LEFT JOIN discovery_queue q ON q.queue_id=s.queue_id
        LEFT JOIN catalysts ca ON ca.catalyst_id=q.catalyst_id
        LEFT JOIN research_thesis_outcomes o ON o.evaluation_id=c.evaluation_id
        WHERE c.route=?'''
    values = [ROUTE]
    if since:
        query += ' AND julianday(c.created_at)>=julianday(?)'
        values.append(since)
    if unresolved_only:
        query += " AND (o.status IS NULL OR o.status!='complete')"
    query += ' ORDER BY c.created_at'
    if limit:
        query += ' LIMIT ?'
        values.append(limit)
    rows = []
    for row in conn.execute(query, values):
        check = json.loads(row['record_json'])
        if (check.get('thesis') or {}).get('direction') not in SIGN:
            continue
        rows.append({'check': check, 'symbol': row['symbol'], 'session': json.loads(row['session_json'] or '{}'),
                     'catalyst': json.loads(row['catalyst_json'] or '{}'), 'outcome_status': row['outcome_status']})
    return rows


def research_outcome_symbols(*, db_path=DB_PATH):
    """Tickers whose thesis outcomes still need price history."""
    with closing(get_connection(db_path)) as conn:
        return sorted({row['symbol'] for row in _theses(conn, unresolved_only=True)})


def collect_research_thesis_outcomes(*, as_of=None, db_path=DB_PATH):
    initialize_database(db_path=db_path)
    at = utc(as_of or datetime.now(timezone.utc))
    with closing(get_connection(db_path)) as conn:
        pending = _theses(conn, unresolved_only=True, limit=BATCH)
    statuses = Counter()
    for row in pending:
        check, thesis = row['check'], row['check']['thesis']
        horizon = outcome_horizon(thesis)
        sector = sector_symbol(row['catalyst']) if row['catalyst'] else None
        histories = load_histories(row['symbol'], sector, as_of=at, db_path=db_path)
        response = measure_response(histories, event_at=check['created_at'], as_of=at, horizon_days=horizon)
        raw = response.get('residual_return')
        record = {'outcome_version': OUTCOME_VERSION, 'evaluation_id': check['evaluation_id'],
                  'symbol': row['symbol'], 'direction': thesis['direction'], 'check_status': check['status'],
                  'decision_at': check['created_at'], 'horizon_days': horizon,
                  'stated_horizon': thesis.get('horizon'), 'status': response['status'],
                  'raw_residual_return': raw,
                  'signed_residual_return': raw * SIGN[thesis['direction']] if raw is not None else None,
                  'outcome_at': response.get('outcome_at'), 'sector_symbol': sector}
        statuses[response['status']] += 1
        with closing(get_connection(db_path)) as conn, conn:
            conn.execute('''INSERT INTO research_thesis_outcomes VALUES (?,?,?,?)
                ON CONFLICT(evaluation_id) DO UPDATE SET status=excluded.status, recorded_at=excluded.recorded_at,
                record_json=excluded.record_json WHERE research_thesis_outcomes.status!='complete' ''',
                         (check['evaluation_id'], response['status'], at.isoformat(), canonical(record)))
    return {'checked': len(pending), 'statuses': dict(statuses)}


def _summary(outcomes):
    resolved = [row['signed_residual_return'] for row in outcomes if row['signed_residual_return'] is not None]
    return {'theses': len(outcomes), 'resolved': len(resolved),
            'mean_signed_residual_return': mean(resolved) if resolved else None,
            'hit_rate': sum(value > 0 for value in resolved) / len(resolved) if resolved else None}


def research_route_report(*, days=30, as_of=None, db_path=DB_PATH):
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn:
        return research_route_summary(conn, days=days, as_of=as_of)


def research_route_summary(conn, *, days=30, as_of=None):
    """Read-only: thesis outcomes by check status, with what the research cost and how long it took."""
    at = utc(as_of or datetime.now(timezone.utc))
    since = (at - timedelta(days=days)).isoformat() if days else None
    theses = _theses(conn, since=since)
    stored = {row['evaluation_id']: json.loads(row['record_json']) for row in conn.execute(
        'SELECT evaluation_id, record_json FROM research_thesis_outcomes')}
    by_status, latency, event_latency, cost, calls, versions = defaultdict(list), [], [], 0.0, 0, Counter()
    for row in theses:
        check, session = row['check'], row['session']
        outcome = stored.get(check['evaluation_id']) or {'signed_residual_return': None, 'status': 'not_collected'}
        by_status[check['status']].append(outcome)
        if session:
            cost += session.get('cost_consumed') or 0
            calls += session.get('calls_consumed') or 0
            versions[f"{session.get('model')}|{session.get('prompt_version')}"] += 1
            latency.append((utc(check['created_at']) - utc(session['started_at'])).total_seconds())
        if row['catalyst'].get('first_public_time'):
            event_latency.append((utc(check['created_at']) - utc(row['catalyst']['first_public_time'])).total_seconds())
    return {'window_days': days or None, 'theses': len(theses),
            'outcomes_by_check_status': {status: _summary(rows) for status, rows in sorted(by_status.items())},
            'outcome_statuses': dict(Counter(outcome['status'] for rows in by_status.values() for outcome in rows)),
            'research_cost': {'reserved_cost_usd': cost, 'model_and_retrieval_calls': calls,
                              'note': 'conservative reservations from research sessions, not provider billing'},
            'latency_seconds': {'session_to_thesis_median': median(latency) if latency else None,
                                'event_to_thesis_median': median(event_latency) if event_latency else None},
            'model_prompt_versions': dict(versions),
            'basis': 'residual stock return in the thesis direction from thesis time; no option or cost model'}
