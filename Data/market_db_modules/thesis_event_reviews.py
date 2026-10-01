"""Immutable event-triggered thesis reviews, the latest verdict per thesis, and a shadow summary."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json

from db_common import DB_PATH, get_connection
from research_foundation_contracts import timestamp
from .bootstrap import initialize_database
from .foundation_store import canonical, digest, insert_record


REVIEWED = 'evaluated'


def thesis_event_review_id(thesis_id, queue_id):
    return 'event-review:' + digest([int(thesis_id), str(queue_id)])


def record_thesis_event_review(review, *, db_path=DB_PATH):
    initialize_database(db_path=db_path)
    data = json.loads(canonical({**review, 'reviewed_at': timestamp(review['reviewed_at'])}))
    data['review_id'] = thesis_event_review_id(data['thesis_id'], data['queue_id'])
    with closing(get_connection(db_path)) as conn, conn:
        return insert_record(conn, 'thesis_event_reviews', 'review_id', data, {
            'thesis_id': data['thesis_id'], 'queue_id': data['queue_id'], 'option_symbol': data['option_symbol'],
            'status': data['status'], 'evidence_hash': data['evidence_hash'], 'review_day': data['review_day'],
            'reviewed_at': data['reviewed_at'], 'thesis_state': data.get('thesis_state'),
            'would_be_action': data.get('would_be_action')})


def _records(conn, sql, values):
    records = []
    for row in conn.execute(sql, values):
        record = json.loads(row['record_json'])
        if digest(record) != row['record_hash']:
            raise ValueError('Corrupt thesis_event_reviews record hash')
        records.append(record)
    return records


def find_thesis_event_review(review_id, *, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        found = _records(conn, 'SELECT * FROM thesis_event_reviews WHERE review_id=?', (review_id,))
    return found[0] if found else None


def thesis_event_reviews(thesis_id, *, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        return _records(conn, 'SELECT * FROM thesis_event_reviews WHERE thesis_id=? ORDER BY reviewed_at',
                        (int(thesis_id),))


def latest_thesis_verdict(thesis_id, *, db_path=DB_PATH):
    """The most recent adjudicated verdict from either the daily validator or an event review."""
    with closing(get_connection(db_path)) as conn:
        daily = conn.execute('''SELECT id, run_at, thesis_state, would_open_today FROM thesis_validation_runs
            WHERE thesis_id=? AND thesis_state NOT IN ('not_evaluated') AND COALESCE(consistency,'')!='error'
            ORDER BY run_at DESC LIMIT 1''', (int(thesis_id),)).fetchone()
        event = conn.execute(f'''SELECT review_id, reviewed_at, thesis_state, record_json FROM thesis_event_reviews
            WHERE thesis_id=? AND status='{REVIEWED}' ORDER BY reviewed_at DESC LIMIT 1''', (int(thesis_id),)).fetchone()
    candidates = []
    if daily:
        candidates.append({'source': 'daily_validator', 'reference': daily['id'], 'reviewed_at': timestamp(daily['run_at']),
                           'thesis_state': daily['thesis_state'], 'would_open_today': daily['would_open_today']})
    if event:
        candidates.append({'source': 'event_review', 'reference': event['review_id'], 'reviewed_at': event['reviewed_at'],
                           'thesis_state': event['thesis_state'],
                           'would_open_today': json.loads(event['record_json']).get('would_open_today')})
    return max(candidates, key=lambda item: item['reviewed_at']) if candidates else None


def thesis_event_review_summary(*, days=7, db_path=DB_PATH):
    """Descriptive shadow-period counts; no review here ever executed an exit."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat() if days else None
    with closing(get_connection(db_path)) as conn:
        records = _records(conn, 'SELECT * FROM thesis_event_reviews' +
                           (' WHERE julianday(reviewed_at)>=julianday(?)' if since else '') + ' ORDER BY reviewed_at',
                           (since,) if since else ())
    evaluated = [record for record in records if record['status'] == REVIEWED]
    return {'window_days': days or None, 'reviews': len(records),
            'statuses': dict(Counter(record['status'] for record in records).most_common()),
            'thesis_states': dict(Counter(record.get('thesis_state') for record in evaluated).most_common()),
            'would_be_actions': dict(Counter(record.get('would_be_action') for record in evaluated).most_common()),
            'changed_verdicts': sum(bool((record.get('change') or {}).get('changed')) for record in evaluated),
            'cited_new_evidence': sum(bool((record.get('change') or {}).get('cited_trigger_article_ids'))
                                      for record in evaluated),
            'authority': 'shadow only: would-be actions were never executed'}
