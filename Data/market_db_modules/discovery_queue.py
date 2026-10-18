"""Atomic queue leases with fencing tokens; no network/model calls in transactions."""
from contextlib import closing
from datetime import datetime, timedelta
import json
from uuid import uuid4

from db_common import get_connection
from research_foundation_contracts import timestamp
from .foundation_store import canonical, digest, insert_record, record_data


def enqueue_discovery_event(item, event, *, expires_at, db_path=None):
    data = record_data(item)
    identity = {key: data[key] for key in ('company_id', 'catalyst_id', 'evidence_revision', 'purpose')}
    queue_id = 'queue:' + digest(identity)
    data['queue_id'] = queue_id
    if (event['company']['id'] != item.company_id or event['catalyst_id'] != item.catalyst_id
            or event['evidence_revision'] != item.evidence_revision or timestamp(event['observed_at']) > data['created_at']):
        raise ValueError('Queue item and frozen company event do not match')
    if timestamp(expires_at) <= data['created_at']:
        raise ValueError('Discovery item must have a future expiry')
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        latest = conn.execute('''SELECT observed_at FROM discovery_events WHERE company_id=? AND catalyst_id=?
            ORDER BY observed_at DESC LIMIT 1''', (item.company_id, item.catalyst_id)).fetchone()
        if latest and timestamp(latest[0]) > data['created_at']:
            raise ValueError('Cannot enqueue a historical revision after a newer event')
        if event.get('package_id'):
            package = conn.execute('SELECT * FROM evidence_packages WHERE package_id=?', (event['package_id'],)).fetchone()
            if (not package or package['company_id'] != item.company_id or package['catalyst_id'] != item.catalyst_id
                    or package['event_revision'] != item.evidence_revision
                    or timestamp(package['information_cutoff_time']) > data['created_at']):
                raise ValueError('Discovery package does not match queue identity or cutoff')
        known = conn.execute('''SELECT 1 FROM discovery_events
            WHERE company_id=? AND catalyst_id=? AND evidence_revision=?''',
                             (item.company_id, item.catalyst_id, item.evidence_revision)).fetchone()
        if known is None:
            conn.execute('''UPDATE discovery_queue SET status='expired',completion_reason='superseded',
                lease_owner=NULL,lease_expires_at=NULL WHERE company_id=? AND catalyst_id=?
                AND evidence_revision!=? AND status IN ('pending','deferred','running')''',
                         (item.company_id, item.catalyst_id, item.evidence_revision))
        conn.execute('''INSERT OR IGNORE INTO discovery_events VALUES (?,?,?,?,?,?,?)''',
                     (item.company_id, item.catalyst_id, item.evidence_revision, data['created_at'],
                      event['change_kind'], canonical(event), digest(event)))
        if not conn.execute('SELECT 1 FROM discovery_queue WHERE queue_id=?', (queue_id,)).fetchone():
            insert_record(conn, 'discovery_queue', 'queue_id', data,
                          {**identity, 'next_attempt_at': data['created_at']})
        conn.execute('INSERT OR IGNORE INTO discovery_queue_details VALUES (?,?,?,?,NULL,1)',
                     (queue_id, timestamp(expires_at), sum(item.priority_components.values()), canonical(item.priority_components)))
        conn.execute('INSERT OR IGNORE INTO discovery_queue_origins VALUES (?,?)', (queue_id, item.origin))
        stored = conn.execute('SELECT priority_json FROM discovery_queue_details WHERE queue_id=?', (queue_id,)).fetchone()
        combined = json.loads(stored[0])
        for key, value in item.priority_components.items():
            combined[key] = max(value, combined.get(key, 0))
        conn.execute('UPDATE discovery_queue_details SET priority_json=?,base_priority=? WHERE queue_id=?',
                     (canonical(combined), sum(combined.values()), queue_id))
    return queue_id


def _row_payload(conn, row, at, aging_per_hour):
    result = dict(row)
    initial = json.loads(result.pop('record_json'))
    if digest(initial) != result['record_hash']:
        raise ValueError('Corrupt discovery queue record')
    result.update(origin=initial['origin'], created_at=initial['created_at'])
    result['priority_components'] = json.loads(result.pop('priority_json'))
    waited = max(0, (datetime.fromisoformat(at) - datetime.fromisoformat(initial['created_at'])).total_seconds())
    result['priority_components']['waiting_time'] = waited / 3600 * aging_per_hour
    result['priority'] = sum(result['priority_components'].values())
    result['origins'] = [r[0] for r in conn.execute('SELECT origin FROM discovery_queue_origins WHERE queue_id=? ORDER BY origin', (row['queue_id'],))]
    event = conn.execute('SELECT * FROM discovery_events WHERE company_id=? AND catalyst_id=? AND evidence_revision=?',
                         (row['company_id'], row['catalyst_id'], row['evidence_revision'])).fetchone()
    if event:
        result['event'] = json.loads(event['payload_json'])
        if digest(result['event']) != event['payload_hash']:
            raise ValueError('Corrupt discovery event')
    else:
        raise ValueError('Discovery item has no frozen event')
    return result


REVIEW_PURPOSE = 'position_review'
# Position reviews have their own consumer in the option manager; the discovery worker never claims them.
DISCOVERY_WORK = "q.purpose != 'position_review'"
REVIEW_WORK = "q.purpose = 'position_review'"


def _ranked_due(conn, at, aging_per_hour, work=DISCOVERY_WORK):
    rows = conn.execute(f'''SELECT q.*,d.* FROM discovery_queue q JOIN discovery_queue_details d USING(queue_id)
        WHERE q.status IN ('pending','deferred') AND julianday(q.next_attempt_at)<=julianday(?)
        AND julianday(d.expires_at)>julianday(?) AND {work}''', (at, at)).fetchall()
    ranked = [_row_payload(conn, row, at, aging_per_hour) for row in rows]
    return sorted(ranked, key=lambda row: (-row['priority'], row['created_at'], row['queue_id']))


def preview_discovery_queue(*, at, limit=20, aging_per_hour=1, db_path=None):
    with closing(get_connection(db_path)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='discovery_queue_details'").fetchone():
            return []
        return _ranked_due(conn, timestamp(at), aging_per_hour)[:limit]


def _recover_leases(conn, at, policy):
    conn.execute('''UPDATE discovery_queue SET status='expired',completion_reason='stale_evidence',
        lease_owner=NULL,lease_expires_at=NULL WHERE status IN ('pending','deferred','running')
        AND queue_id IN (SELECT queue_id FROM discovery_queue_details WHERE julianday(expires_at)<=julianday(?))''', (at,))
    conn.execute('''UPDATE discovery_queue SET status=CASE WHEN attempts>=? THEN 'failed' ELSE 'pending' END,
        completion_reason='lease_expired',lease_owner=NULL,lease_expires_at=NULL,next_attempt_at=?
        WHERE status='running' AND julianday(lease_expires_at)<=julianday(?)''', (policy.max_attempts, at, at))
    conn.execute('''UPDATE discovery_queue SET status='failed',completion_reason='retry_exhausted'
        WHERE status IN ('pending','deferred') AND attempts>=?''', (policy.max_attempts,))
    conn.execute('''UPDATE discovery_queue_details SET lease_token=NULL WHERE queue_id IN
        (SELECT queue_id FROM discovery_queue WHERE status!='running')''')


def _reserve_batch(ranked, policy):
    selected = [row for row in ranked if 'direct_event' in row['origins']][:policy.direct_slots]
    selected.extend([row for row in ranked if row not in selected][:policy.batch_size - len(selected)])
    return selected


def _lease(conn, rows, owner, *, at, policy):
    expiry = (datetime.fromisoformat(at) + timedelta(seconds=policy.lease_seconds)).isoformat()
    for row in rows:
        token = uuid4().hex
        conn.execute('''UPDATE discovery_queue SET status='running',attempts=attempts+1,
            lease_owner=?,lease_expires_at=?,completion_reason=NULL WHERE queue_id=?''', (owner, expiry, row['queue_id']))
        conn.execute('UPDATE discovery_queue_details SET lease_token=? WHERE queue_id=?', (token, row['queue_id']))
        row.update(lease_owner=owner, lease_token=token, lease_expires_at=expiry, attempts=row['attempts'] + 1, status='running')
    return rows


def claim_discovery_batch(owner, *, at, policy, db_path=None):
    if not owner.strip():
        raise ValueError('A queue worker needs an owner identity')
    at = timestamp(at)
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        _recover_leases(conn, at, policy)
        return _lease(conn, _reserve_batch(_ranked_due(conn, at, policy.aging_per_hour), policy), owner, at=at, policy=policy)


def claim_position_reviews(owner, *, at, policy, debounce_seconds, limit, db_path=None):
    """Lease settled position-review items, highest priority first.

    An item settles once it is `debounce_seconds` old. A newer revision of the same event
    expires the older item, so a burst of updates is reviewed once, at its latest revision.
    """
    if not owner.strip():
        raise ValueError('A queue worker needs an owner identity')
    at = timestamp(at)
    settled_before = datetime.fromisoformat(at) - timedelta(seconds=debounce_seconds)
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='discovery_queue_details'").fetchone():
            return []
        _recover_leases(conn, at, policy)
        settled = [row for row in _ranked_due(conn, at, policy.aging_per_hour, REVIEW_WORK)
                   if datetime.fromisoformat(timestamp(row['created_at'])) <= settled_before]
        return _lease(conn, settled[:limit], owner, at=at, policy=policy)


def require_discovery_lease(conn, queue_id, token, at):
    row = conn.execute('''SELECT q.*,d.lease_token,d.expires_at FROM discovery_queue q
        JOIN discovery_queue_details d USING(queue_id) WHERE queue_id=?''', (queue_id,)).fetchone()
    if (not row or row['status'] != 'running' or row['lease_token'] != token
            or timestamp(row['lease_expires_at']) <= timestamp(at) or timestamp(row['expires_at']) <= timestamp(at)):
        raise ValueError('Discovery lease is stale or owned by another worker')
    return row


def renew_discovery_lease(queue_id, token, *, at, seconds, db_path=None):
    if seconds <= 0:
        raise ValueError('Lease extension must be positive')
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        row = require_discovery_lease(conn, queue_id, token, at)
        expiry = min(row['expires_at'], (datetime.fromisoformat(timestamp(at)) + timedelta(seconds=seconds)).isoformat())
        conn.execute('UPDATE discovery_queue SET lease_expires_at=? WHERE queue_id=?', (expiry, queue_id))
    return expiry


def finish_discovery_item(queue_id, token, *, status, reason, at, policy, db_path=None):
    if status not in {'completed', 'deferred', 'expired', 'failed'} or not reason.strip():
        raise ValueError('Queue completion needs a valid status and reason')
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        row = require_discovery_lease(conn, queue_id, token, at)
        if status == 'deferred' and row['attempts'] >= policy.max_attempts:
            status, reason = 'failed', 'retry_exhausted:' + reason
        delay = policy.retry_seconds * 2 ** min(row['attempts'] - 1, 10)
        due = (datetime.fromisoformat(timestamp(at)) + timedelta(seconds=delay)).isoformat()
        conn.execute('''UPDATE discovery_queue SET status=?,completion_reason=?,next_attempt_at=?,
            lease_owner=NULL,lease_expires_at=NULL WHERE queue_id=?''', (status, reason, due, queue_id))
        conn.execute('UPDATE discovery_queue_details SET lease_token=NULL WHERE queue_id=?', (queue_id,))
    return status


def discovery_queue_status(*, db_path=None):
    with closing(get_connection(db_path)) as conn:
        return [dict(row) for row in conn.execute('''SELECT q.queue_id,q.company_id,c.symbol,q.purpose,q.status,
            q.attempts,q.next_attempt_at,q.lease_owner,q.lease_expires_at,q.completion_reason,d.expires_at,
            s.candidate_id,s.admission_status FROM discovery_queue q JOIN companies c ON c.id=q.company_id
            JOIN discovery_queue_details d USING(queue_id) LEFT JOIN discovery_shadow_candidates s USING(queue_id)
            ORDER BY q.rowid''')]
