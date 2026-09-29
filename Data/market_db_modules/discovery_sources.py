"""Read the configured universe and existing provenance without changing news clusters."""
from contextlib import closing
from datetime import datetime, timedelta
import json

from db_common import DB_PATH, get_connection
from .company_evidence import latest_evidence_package


def table_exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def discovery_companies(symbols, *, db_path=DB_PATH):
    if not symbols:
        return []
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute('''SELECT c.id,c.symbol,c.name,i.industry_key,i.name AS industry_name,s.sector_key
            FROM companies c JOIN industries i ON i.id=c.industry_id JOIN sectors s ON s.id=i.sector_id ORDER BY c.id''')
        return [dict(row) for row in rows if '*' in symbols or row['symbol'].upper() in symbols]


def review_company_ids(*, at, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        positions = {row[0] for row in conn.execute('''SELECT c.id FROM position_theses p JOIN companies c
            ON UPPER(c.symbol)=UPPER(p.symbol) WHERE p.status='open' AND julianday(p.opened_at)<=julianday(?)''', (at,))}
        watchlist = set()
        if table_exists(conn, 'strategist_company_summaries'):
            watchlist = {row[0] for row in conn.execute('''SELECT company_id FROM strategist_company_summaries
                WHERE decision='watchlist' AND julianday(updated_at)<=julianday(?)''', (at,))}
    return positions, watchlist


def company_event_articles(company_id, *, at, stale_seconds, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        if not table_exists(conn, 'news_articles') or not table_exists(conn, 'company_news_articles'):
            return []
        return [dict(row) for row in conn.execute('''SELECT DISTINCT a.* FROM news_articles a
            JOIN company_news_articles l ON l.article_id=a.id JOIN companies c ON c.id=l.company_id
            WHERE l.company_id=? AND UPPER(a.primary_ticker)=UPPER(c.symbol) AND a.catalyst_id IS NOT NULL
            AND julianday(COALESCE(a.first_seen_time,a.created_at))<=julianday(?)
            AND julianday(a.event_time) BETWEEN julianday(?)-?/86400.0 AND julianday(?)
            ORDER BY a.id''', (company_id, at, at, stale_seconds, at))]


def select_discovery_companies(policy, *, at, lane='events', limit=None, db_path=DB_PATH):
    """Reserve half the scan for least-recently-seen companies; prioritize the rest."""
    companies = discovery_companies(policy.symbols, db_path=db_path)
    limit = min(limit or policy.scan_limit, policy.scan_limit)
    if not companies:
        return []
    positions, watchlist = review_company_ids(at=at, db_path=db_path)
    with closing(get_connection(db_path)) as conn:
        seen = {row['company_id']: row['scanned_at'] for row in conn.execute('SELECT * FROM discovery_scan_state WHERE lane=?', (lane,))}
        schedules = {row[0]: json.loads(row[1] or '[]') for row in conn.execute('''WITH dated AS (
            SELECT company_id,record_json,ROW_NUMBER() OVER (PARTITION BY company_id
                ORDER BY julianday(information_cutoff_time) DESC,rowid DESC) AS position
            FROM evidence_packages WHERE julianday(information_cutoff_time)<=julianday(?))
            SELECT company_id,json_extract(record_json,'$.scheduled_events') FROM dated WHERE position=1''', (at,))}
        news = {}
        if table_exists(conn, 'company_news_articles'):
            news = {row[0]: row[1] for row in conn.execute('''SELECT l.company_id,MAX(COALESCE(a.first_seen_time,a.created_at))
                FROM news_articles a JOIN company_news_articles l ON l.article_id=a.id
                WHERE julianday(COALESCE(a.first_seen_time,a.created_at))<=julianday(?)
                AND julianday(a.event_time) BETWEEN julianday(?)-?/86400.0 AND julianday(?) GROUP BY l.company_id''',
                                                         (at, at, policy.stale_seconds, at))}
    rotation = sorted(companies, key=lambda c: (seen.get(c['id'], ''), c['id']))[:max(1, (limit + 1) // 2)]
    priorities = {}
    now = datetime.fromisoformat(at)
    for company in companies:
        identity = company['id']
        priority = 3 * (identity in positions) + (identity in watchlist)
        for event in schedules.get(identity, []):
            if event.get('superseded') or not event.get('confirmed'):
                continue
            date_text = str(event.get('scheduled_at', ''))[:10]
            if now.date().isoformat() <= date_text <= (now + timedelta(days=7)).date().isoformat():
                priority += 4
        if news.get(identity, '') > seen.get(identity, ''):
            priority += 5
        priorities[identity] = priority
    urgent = sorted((c for c in companies if c not in rotation),
                    key=lambda c: (-priorities[c['id']], seen.get(c['id'], ''), c['id']))
    selected = rotation + urgent[:max(0, limit - len(rotation))]
    return selected


def mark_discovery_scanned(company_id, *, at, lane='events', db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('''INSERT INTO discovery_scan_state VALUES (?,?,?) ON CONFLICT(lane,company_id)
            DO UPDATE SET scanned_at=excluded.scanned_at''', (lane, company_id, at))


def get_catalyst(catalyst_id, *, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        row = conn.execute('SELECT state_json FROM catalysts WHERE catalyst_id=?', (catalyst_id,)).fetchone()
    return json.loads(row[0]) if row else None


def latest_discovery_event(company_id, catalyst_id, *, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        row = conn.execute('''SELECT * FROM discovery_events WHERE company_id=? AND catalyst_id=?
            ORDER BY observed_at DESC,rowid DESC LIMIT 1''', (company_id, catalyst_id)).fetchone()
    return dict(row) if row else None


def sector_support_articles(company_id, *, at, stale_seconds, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        if not table_exists(conn, 'company_opportunist_impacts'):
            return []
        return [dict(row) for row in conn.execute('''SELECT a.* FROM company_opportunist_impacts i
            JOIN news_articles a ON a.id=i.article_id WHERE i.company_id=? AND i.confidence='high'
            AND julianday(i.created_at)<=julianday(?) AND julianday(COALESCE(a.first_seen_time,a.created_at))<=julianday(?)
            AND julianday(a.published_at) BETWEEN julianday(?)-?/86400.0 AND julianday(?) ORDER BY a.id''',
                                                (company_id, at, at, at, stale_seconds, at))]


def watchlist_snapshot(company_id, *, at, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        if not table_exists(conn, 'strategist_company_summaries'):
            return None
        row = conn.execute('''SELECT decision,confidence,summary,thesis,risks,updated_at FROM strategist_company_summaries
            WHERE company_id=? AND decision='watchlist' AND julianday(updated_at)<=julianday(?)''', (company_id, at)).fetchone()
    return dict(row) if row else None
