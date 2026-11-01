"""Reconcile current managed companies without treating collection as new interest."""
from contextlib import closing

from db_common import get_connection
from market_db_modules.company_interest import register_interest, signal_discovery
from market_db_modules.discovery_sources import review_company_ids, table_exists
from market_db_modules.foundation_store import digest


def reconcile_company_interest(*, at, lifetime_seconds, db_path):
    positions, watchlist = review_company_ids(at=at, db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        # One-time adoption preserves pre-upgrade demand, using its original age.
        migrated = conn.execute("SELECT 1 FROM company_collection_state WHERE provider='interest' AND scope='migration-v1'").fetchone()
        if not migrated:
            for row in conn.execute('''SELECT company_id,source,source_key,requested_at FROM company_evidence_requests
                WHERE source IN ('pipeline_selection','opportunist','research_candidate','research_gap')
                AND julianday(requested_at) BETWEEN julianday(?)-?/86400.0 AND julianday(?)
                ORDER BY requested_at DESC''',
                                    (at, lifetime_seconds, at)).fetchall():
                if conn.execute('SELECT 1 FROM company_interest WHERE company_id=? AND source=?',
                                (row['company_id'], row['source'])).fetchone():
                    continue
                register_interest(conn, row['company_id'], row['source'], 'pipeline' if row['source'] == 'pipeline_selection' else row['source_key'],
                                  at=row['requested_at'], lifetime_seconds=lifetime_seconds)
            conn.execute("INSERT INTO company_collection_state VALUES ('interest','migration-v1','{}',?)", (at,))
        for source, identities in (('position_review', positions), ('watchlist', watchlist)):
            for row in conn.execute('SELECT company_id FROM company_interest WHERE source=?', (source,)).fetchall():
                if row[0] not in identities:
                    conn.execute('UPDATE company_interest SET expires_at=? WHERE company_id=? AND source=?', (at, row[0], source))
            for company_id in identities:
                register_interest(conn, company_id, source, 'current', at=at, lifetime_seconds=lifetime_seconds)
        if table_exists(conn, 'strategist_company_summaries'):
            for row in conn.execute("SELECT * FROM strategist_company_summaries WHERE decision='watchlist'").fetchall():
                if row['company_id'] in watchlist:
                    content = {key: row[key] for key in ('decision', 'summary', 'thesis', 'risks', 'confidence')}
                    signal_discovery(conn, row['company_id'], 'watchlist', digest(content), at=at)
        for row in conn.execute('''SELECT s.session_id,q.company_id FROM research_sessions s
            JOIN discovery_queue q ON q.queue_id=s.queue_id
            WHERE julianday(json_extract(s.record_json,'$.started_at'))<=julianday(?)
            AND (s.status IN ('pending','running') OR (s.status='deferred_missing_evidence' AND EXISTS (
                SELECT 1 FROM company_evidence_requests r WHERE r.parent_session_id=s.session_id
                AND (r.status='pending' OR (r.status='completed' AND r.reason IS NULL
                    AND NOT EXISTS (SELECT 1 FROM research_evidence_continuations c WHERE c.request_id=r.request_id))))))''', (at,)).fetchall():
            register_interest(conn, row['company_id'], 'active_research', row['session_id'],
                              at=at, lifetime_seconds=lifetime_seconds)
    return positions, watchlist
