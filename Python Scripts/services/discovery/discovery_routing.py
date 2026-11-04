"""Admitted discovery entries that DISCOVERY_MODE=enforce sends to the production strategist and manager.

Admission (evidence score and freshness) already happened in the discovery queue; research readiness
is not required. Each admitted event is routed once.
"""
from contextlib import closing
import json

from db_common import get_connection


def admitted_entries(*, policy, at, exclude_symbols=(), db_path=None):
    """The highest-priority ready entry events not yet routed, one per company, at most `production_limit`."""
    if policy.mode != 'enforce' or policy.production_limit == 0:
        return []
    excluded = {symbol.upper() for symbol in exclude_symbols}
    entries = {}
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute('''SELECT s.queue_id,s.payload_json,q.company_id,q.catalyst_id,c.symbol,c.name,
                i.industry_key,i.name AS industry_name,se.sector_key
            FROM discovery_shadow_candidates s JOIN discovery_queue q ON q.queue_id=s.queue_id
            JOIN discovery_queue_details d ON d.queue_id=s.queue_id
            JOIN companies c ON c.id=q.company_id JOIN industries i ON i.id=c.industry_id
            JOIN sectors se ON se.id=i.sector_id
            WHERE s.admission_status='ready' AND q.purpose='entry' AND q.status='completed'
            AND julianday(s.created_at)<=julianday(?) AND julianday(d.expires_at)>julianday(?)
            AND NOT EXISTS (SELECT 1 FROM discovery_production_routes r WHERE r.queue_id=s.queue_id)
            ORDER BY d.base_priority DESC, s.created_at, s.queue_id''', (at, at)).fetchall()
        for row in rows:
            symbol = row['symbol'].upper()
            if symbol in excluded or symbol in entries:
                continue
            if len(entries) >= policy.production_limit:
                break
            origins = [r[0] for r in conn.execute(
                'SELECT origin FROM discovery_queue_origins WHERE queue_id=? ORDER BY origin', (row['queue_id'],))]
            sources = [r[0] for r in conn.execute('''SELECT DISTINCT source FROM company_interest WHERE company_id=?
                AND julianday(selected_at)<=julianday(?) AND julianday(expires_at)>julianday(?) ORDER BY source''',
                                                  (row['company_id'], at, at))]
            entries[symbol] = {
                'symbol': symbol, 'company_id': row['company_id'], 'name': row['name'],
                'sector_key': row['sector_key'], 'industry_key': row['industry_key'],
                'industry_name': row['industry_name'], 'queue_id': row['queue_id'], 'catalyst_id': row['catalyst_id'],
                'discovery_origins': origins, 'interest_sources': sources,
                'admission': json.loads(row['payload_json'])['admission']}
    return list(entries.values())


def record_routes(routes, *, at, db_path=None):
    with closing(get_connection(db_path)) as conn, conn:
        conn.executemany('INSERT OR IGNORE INTO discovery_production_routes VALUES (?,?,?,?)',
                         [(route['queue_id'], route['symbol'], route['candidate_id'], at) for route in routes])
