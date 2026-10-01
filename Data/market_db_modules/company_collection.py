"""Durable provider cache, identifiers and request accounting; no network I/O."""
from contextlib import closing
from hashlib import sha256
import json

from db_common import DB_PATH, get_connection
from research_foundation_contracts import timestamp
from .foundation_store import canonical, digest


def cached_response(provider, url, *, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        row = conn.execute("""SELECT * FROM company_provider_responses WHERE provider=? AND url=?
            ORDER BY observed_at DESC,rowid DESC LIMIT 1""", (provider, url)).fetchone()
    if row is None:
        return None
    result = dict(row)
    if sha256(result['body'].encode()).hexdigest() != result['content_hash']:
        raise ValueError('Corrupt provider response')
    return result


def cache_response(provider, url, body, observed_at, *, db_path=DB_PATH):
    content_hash = sha256(body.encode()).hexdigest()
    identity = 'response:' + digest([provider, url, content_hash])
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('INSERT OR IGNORE INTO company_provider_responses VALUES (?,?,?,?,?,?)',
                     (identity, provider, url, timestamp(observed_at), body, content_hash))
        row = conn.execute('SELECT * FROM company_provider_responses WHERE response_id=?', (identity,)).fetchone()
    return dict(row)


def collection_state(provider, scope, *, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        row = conn.execute('SELECT value_json FROM company_collection_state WHERE provider=? AND scope=?',
                           (provider, scope)).fetchone()
    return json.loads(row['value_json']) if row else {}


def save_collection_state(provider, scope, value, at, *, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('''INSERT INTO company_collection_state VALUES (?,?,?,?)
            ON CONFLICT(provider,scope) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at''',
                     (provider, scope, canonical(value), timestamp(at)))


def reserve_provider_request(provider, at, *, daily_limit, interval_seconds, db_path=DB_PATH):
    """Reserve before dispatch, including retries; serialize collectors sharing this DB."""
    day = at.strftime('%Y-%m-%d')
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM company_provider_usage WHERE provider=? AND day=?',
                           (provider, day)).fetchone()
        count = row['requests'] if row else 0
        if count >= daily_limit:
            raise RuntimeError('Provider daily request budget exhausted')
        previous = conn.execute('SELECT MAX(next_request_at) FROM company_provider_usage WHERE provider=?',
                                (provider,)).fetchone()[0] or 0
        slot = max(at.timestamp(), previous)
        conn.execute('''INSERT INTO company_provider_usage VALUES (?,?,?,?)
            ON CONFLICT(provider,day) DO UPDATE SET requests=excluded.requests,next_request_at=excluded.next_request_at''',
                     (provider, day, count + 1, slot + interval_seconds))
    return slot - at.timestamp()


def update_issuer_mapping(company_id, symbol, cik, source, at, *, db_path=DB_PATH):
    cik = str(cik).zfill(10)
    if not cik.isdigit() or len(cik) != 10 or int(cik) == 0:
        raise ValueError('Invalid SEC issuer identifier')
    with closing(get_connection(db_path)) as conn, conn:
        company = conn.execute('SELECT symbol FROM companies WHERE id=?', (company_id,)).fetchone()
        if not company or company['symbol'].upper() != symbol.upper():
            raise ValueError('Issuer mapping does not match company')
        conn.execute('''INSERT INTO company_issuer_mappings VALUES (?,?,?,?,?)
            ON CONFLICT(company_id) DO UPDATE SET symbol=excluded.symbol,cik=excluded.cik,
                source=excluded.source,updated_at=excluded.updated_at''',
                     (company_id, symbol.upper(), cik, source, timestamp(at)))


def get_issuer_mapping(company_id, *, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        row = conn.execute('SELECT * FROM company_issuer_mappings WHERE company_id=?', (company_id,)).fetchone()
    return dict(row) if row else None
