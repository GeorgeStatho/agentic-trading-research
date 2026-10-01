"""Shared, expiring company interest and coalesced discovery notifications."""
from datetime import datetime, timedelta
from contextlib import closing

from db_common import DB_PATH, get_connection
from research_foundation_contracts import timestamp
from .foundation_store import digest


def active_company_ids(conn, *, at):
    return {row[0] for row in conn.execute('''SELECT DISTINCT company_id FROM company_interest
        WHERE julianday(selected_at)<=julianday(?) AND julianday(expires_at)>julianday(?)''', (at, at))}


def register_interest(conn, company_id, source, source_key, *, at, lifetime_seconds):
    at = timestamp(at)
    expiry = (datetime.fromisoformat(at) + timedelta(seconds=lifetime_seconds)).isoformat()
    previous = conn.execute('''SELECT * FROM company_interest
        WHERE company_id=? AND source=? AND source_key=?''', (company_id, source, source_key)).fetchone()
    conn.execute('''INSERT INTO company_interest VALUES (?,?,?,?,?)
        ON CONFLICT(company_id,source,source_key) DO UPDATE SET
        selected_at=excluded.selected_at,expires_at=excluded.expires_at
        WHERE julianday(excluded.selected_at)>=julianday(company_interest.selected_at)''',
                 (company_id, source, source_key, at, expiry))
    if previous is None or timestamp(previous['expires_at']) <= at:
        signal_discovery(conn, company_id, 'interest:' + source + ':' + source_key, at, at=at)


def signal_discovery(conn, company_id, source, version, *, at):
    """A signal never enrolls an unrelated company or renews its interest."""
    at = timestamp(at)
    if not conn.execute('''SELECT 1 FROM company_interest WHERE company_id=?
        AND julianday(selected_at)<=julianday(?) AND julianday(expires_at)>julianday(?)''',
                        (company_id, at, at)).fetchone():
        return
    changed = conn.execute('''INSERT INTO discovery_check_signals VALUES (?,?,?,?)
        ON CONFLICT(company_id,source) DO UPDATE SET version=excluded.version,observed_at=excluded.observed_at
        WHERE version!=excluded.version AND julianday(excluded.observed_at)>=julianday(observed_at)''',
                           (company_id, source, version, at)).rowcount
    if changed:
        conn.execute('''INSERT INTO discovery_checks(company_id,requested_at,next_check_at) VALUES (?,?,?)
            ON CONFLICT(company_id) DO UPDATE SET generation=generation+1,status='pending',
            requested_at=CASE WHEN status='pending' THEN requested_at ELSE excluded.requested_at END,
            next_check_at=excluded.next_check_at,reason=NULL''', (company_id, at, at))


def due_discovery_checks(*, at, limit, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        return [dict(row) for row in conn.execute('''SELECT c.id,c.symbol,c.name,
            i.industry_key,i.name AS industry_name,s.sector_key,d.generation,d.dependency_request_id
            FROM discovery_checks d JOIN companies c ON c.id=d.company_id
            JOIN industries i ON i.id=c.industry_id JOIN sectors s ON s.id=i.sector_id
            WHERE julianday(d.next_check_at)<=julianday(?) AND EXISTS (
                SELECT 1 FROM company_interest interest WHERE interest.company_id=c.id
                AND julianday(interest.selected_at)<=julianday(?) AND julianday(interest.expires_at)>julianday(?))
            ORDER BY d.requested_at,c.id LIMIT ?''', (at, at, at, limit))]


def finish_discovery_check(company, *, at, next_at, reason, dependency=None, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('''UPDATE discovery_checks SET status=?,checked_at=?,next_check_at=?,requested_at=?,reason=?,
            dependency_request_id=? WHERE company_id=? AND generation=?''',
                     ('waiting_for_evidence' if dependency else 'checked', at, next_at, next_at, reason,
                      dependency, company['id'], company['generation']))


def signal_company_news(conn, article_id, *, at):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='company_interest'").fetchone():
        return
    article = conn.execute('SELECT title,body,summary,content_hash,primary_ticker,catalyst_id,root_event_id,source_type FROM news_articles WHERE id=?', (article_id,)).fetchone()
    if article is None:
        return
    version = digest(dict(article))
    for row in conn.execute('SELECT company_id FROM company_news_articles WHERE article_id=?', (article_id,)).fetchall():
        signal_discovery(conn, row[0], 'news:' + str(article_id), version, at=at)
