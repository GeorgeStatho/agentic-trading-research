"""Turn persisted company interest into bounded collector work."""
from contextlib import closing
from datetime import datetime, timezone

from db_common import DB_PATH, get_connection
from services.company_interest import reconcile_company_interest
from market_db_modules.company_interest import active_company_ids, register_interest
from market_db_modules.evidence_refresh import enqueue_refresh
from research_foundation_contracts import timestamp


def enqueue_company_interest(*, at, policy, db_path):
    at = timestamp(at)
    period = int(datetime.fromisoformat(at).timestamp()) // policy.refresh_seconds
    reconcile_company_interest(at=at, lifetime_seconds=policy.stale_seconds, db_path=db_path)
    _discard_superseded_requests(at, db_path)
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('''UPDATE company_evidence_requests SET status='unavailable',reason='interest_expired',completed_at=?
            WHERE status='pending'
            AND julianday(requested_at)<julianday(?)-?/86400.0''', (at, at, policy.stale_seconds))
        for company_id in sorted(active_company_ids(conn, at=at)):
            # Existing explicit requests already cover this cycle. Otherwise maintain
            # the active selection on the collector's normal refresh schedule.
            if not conn.execute('''SELECT 1 FROM company_evidence_requests WHERE company_id=?
                AND (status='pending' OR julianday(requested_at)>julianday(?)-?/86400.0)''',
                                (company_id, at, policy.refresh_seconds)).fetchone():
                enqueue_refresh(conn, company_id, 'active_interest', str(period), at=at)


def request_pipeline_evidence(symbols, *, scope='pipeline', at=None, db_path=DB_PATH):
    """Use the actual selection, including caller overrides, rather than reranking it."""
    from market_db_modules.bootstrap import initialize_database
    from services.company_evidence_policy import CompanyEvidencePolicy
    at = timestamp(at or datetime.now(timezone.utc).isoformat())
    policy = CompanyEvidencePolicy.from_env()
    period = int(datetime.fromisoformat(at).timestamp()) // policy.refresh_seconds
    symbols = {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        rows = []
        if symbols:
            placeholders = ','.join('?' for _ in symbols)
            rows = conn.execute(f'SELECT id,symbol FROM companies WHERE UPPER(symbol) IN ({placeholders})',
                                tuple(sorted(symbols))).fetchall()
        selected = {row['id'] for row in rows}
        previous = conn.execute('''SELECT company_id FROM company_interest WHERE source='pipeline_selection'
            AND source_key=? AND julianday(selected_at)<=julianday(?)''', (scope, at)).fetchall()
        conn.executemany('''UPDATE company_interest SET expires_at=? WHERE company_id=?
            AND source='pipeline_selection' AND source_key=?''',
                         [(at, row[0], scope) for row in previous if row[0] not in selected])
        for row in rows:
            register_interest(conn, row['id'], 'pipeline_selection', scope, at=at, lifetime_seconds=policy.stale_seconds)
        return [enqueue_refresh(conn, row['id'], 'pipeline_selection', str(period), at=at)
                for row in rows if row['symbol'].upper() in symbols]


def _discard_superseded_requests(at, db_path):
    from market_db_modules.research_runtime import research_event_superseded
    with closing(get_connection(db_path)) as conn:
        requests = conn.execute('''SELECT r.request_id,s.queue_id FROM company_evidence_requests r
            JOIN research_sessions s ON s.session_id=r.parent_session_id WHERE r.status='pending' ''').fetchall()
    obsolete = [row['request_id'] for row in requests if research_event_superseded(row['queue_id'], db_path=db_path)]
    with closing(get_connection(db_path)) as conn, conn:
        conn.executemany('''UPDATE company_evidence_requests SET status='unavailable',reason='event_superseded',completed_at=?
            WHERE request_id=? AND status='pending' ''', [(at, identity) for identity in obsolete])
