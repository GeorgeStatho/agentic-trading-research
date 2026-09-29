"""Turn persisted company interest into bounded collector work."""
from contextlib import closing
from datetime import datetime, timezone

from db_common import DB_PATH, get_connection
from market_db_modules.discovery_sources import review_company_ids
from market_db_modules.evidence_refresh import enqueue_refresh
from research_foundation_contracts import timestamp


def enqueue_company_interest(*, at, policy, db_path):
    at = timestamp(at)
    period = int(datetime.fromisoformat(at).timestamp()) // policy.refresh_seconds
    positions, watchlist = review_company_ids(at=at, db_path=db_path)
    _discard_superseded_requests(at, db_path)
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('''UPDATE company_evidence_requests SET status='unavailable',reason='interest_expired',completed_at=?
            WHERE status='pending'
            AND julianday(requested_at)<julianday(?)-?/86400.0''', (at, at, policy.stale_seconds))
        for source, identities in (('position_review', positions), ('watchlist', watchlist)):
            for company_id in sorted(identities):
                enqueue_refresh(conn, company_id, source, str(period), at=at)


def request_pipeline_evidence(symbols, *, at=None, db_path=DB_PATH):
    """Use the actual selection, including caller overrides, rather than reranking it."""
    from market_db_modules.bootstrap import initialize_database
    from services.company_evidence_policy import CompanyEvidencePolicy
    at = timestamp(at or datetime.now(timezone.utc).isoformat())
    policy = CompanyEvidencePolicy.from_env()
    period = int(datetime.fromisoformat(at).timestamp()) // policy.refresh_seconds
    symbols = {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}
    if not symbols:
        return []
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        placeholders = ','.join('?' for _ in symbols)
        rows = conn.execute(f'SELECT id,symbol FROM companies WHERE UPPER(symbol) IN ({placeholders})', tuple(sorted(symbols))).fetchall()
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
