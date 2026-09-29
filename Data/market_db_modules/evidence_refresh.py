"""Durable evidence demand. Provider work belongs to the collector."""
from contextlib import closing
from datetime import datetime, timedelta

from db_common import DB_PATH, get_connection
from research_foundation_contracts import timestamp
from .foundation_store import digest


def enqueue_refresh(conn, company_id, source, source_key, *, at, parent_session_id=None):
    identity = 'refresh:' + digest([company_id, source, source_key])
    conn.execute('''INSERT OR IGNORE INTO company_evidence_requests
        (request_id,company_id,source,source_key,parent_session_id,requested_at,next_attempt_at)
        VALUES (?,?,?,?,?,?,?)''',
                 (identity, company_id, source, source_key, parent_session_id, timestamp(at), timestamp(at)))
    return identity


def request_company_evidence(company_id, source, source_key, *, at, parent_session_id=None, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn, conn:
        return enqueue_refresh(conn, company_id, source, source_key, at=at, parent_session_id=parent_session_id)


def due_refresh_companies(*, at, limit, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        return [dict(row) for row in conn.execute('''SELECT c.id,c.symbol,c.name FROM companies c
            JOIN company_evidence_requests r ON r.company_id=c.id
            WHERE r.status='pending' AND julianday(r.next_attempt_at)<=julianday(?) GROUP BY c.id
            ORDER BY MAX(r.parent_session_id IS NOT NULL) DESC,MIN(r.requested_at),c.id LIMIT ?''', (at, limit))]


def due_refresh_requests(company_id, *, at, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        return [dict(row) for row in conn.execute('''SELECT * FROM company_evidence_requests
            WHERE company_id=? AND status='pending' AND julianday(next_attempt_at)<=julianday(?)''', (company_id, at))]


def finish_refresh_requests(requests, *, at, package_id=None, reason=None, retry_at=None, db_path=DB_PATH):
    """Only finish the captured requests; arrivals during a fetch remain pending."""
    with closing(get_connection(db_path)) as conn, conn:
        for request in requests:
            attempts = request['attempts'] + 1
            status = 'completed' if reason is None else 'unavailable' if attempts >= 3 else 'pending'
            next_at = retry_at or (datetime.fromisoformat(timestamp(at)) + timedelta(seconds=60 * 2 ** attempts)).isoformat()
            conn.execute('''UPDATE company_evidence_requests SET status=?,attempts=?,package_id=?,
                reason=?,next_attempt_at=?,completed_at=? WHERE request_id=? AND status='pending' ''',
                         (status, attempts, package_id, reason, next_at, at if status != 'pending' else None,
                          request['request_id']))


def recent_interest_company_ids(conn, *, at, stale_seconds):
    return {row[0] for row in conn.execute('''SELECT DISTINCT company_id FROM company_evidence_requests
        WHERE julianday(requested_at)<=julianday(?) AND (status='pending' OR julianday(requested_at) BETWEEN julianday(?)-?/86400.0 AND julianday(?))''',
                                          (at, at, stale_seconds, at))}
