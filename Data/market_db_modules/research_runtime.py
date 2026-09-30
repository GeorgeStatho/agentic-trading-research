"""Transactional research journal and daily allowances; no external calls here."""
from contextlib import closing
import json

from db_common import DB_PATH, get_connection
from .foundation_store import canonical, digest, read_record


class ResearchBudgetExceeded(ValueError):
    pass


def _write(conn, table, data):
    conn.execute(f'UPDATE {table} SET record_json=?,record_hash=? WHERE session_id=?',
                 (canonical(data), digest(data), data['session_id']))


def daily_reserved_cost(conn, day):
    """Planner, retrieval and thesis reservations share one daily cost budget."""
    research = conn.execute('SELECT COALESCE(SUM(reserved_cost),0) FROM research_daily_allowances WHERE day=?',
                            (day,)).fetchone()[0]
    has_thesis = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_thesis_allowances'").fetchone()
    thesis = conn.execute('SELECT COALESCE(SUM(reserved_cost),0) FROM research_thesis_allowances WHERE day=?',
                          (day,)).fetchone()[0] if has_thesis else 0
    return research + thesis


def load_research_progress(session_id, *, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        return read_record(conn, 'research_session_progress', 'session_id', session_id)


def initialize_research_progress(progress, *, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('INSERT OR IGNORE INTO research_session_progress VALUES (?,?,?)',
                     (progress['session_id'], digest(progress), canonical(progress)))


def research_checkpoint(session, progress, event, *, at, allowance=None, cost_adjustment=None, refresh_request=None, db_path=DB_PATH):
    """Reserve before dispatch; append the journal and progress in the same transaction."""
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if allowance:
            kind, limit, cost, daily_budget = allowance
            day = at[:10]
            row = conn.execute('SELECT calls FROM research_daily_allowances WHERE day=? AND kind=?',
                               (day, kind)).fetchone()
            spent = daily_reserved_cost(conn, day)
            if (row and row['calls'] >= limit) or spent + cost > daily_budget:
                raise ResearchBudgetExceeded('daily_' + kind + '_allowance')
            conn.execute('''INSERT INTO research_daily_allowances VALUES (?,?,1,?)
                ON CONFLICT(day,kind) DO UPDATE SET calls=calls+1,reserved_cost=reserved_cost+excluded.reserved_cost''',
                         (day, kind, cost))
        if cost_adjustment:
            day, extra = cost_adjustment
            conn.execute("UPDATE research_daily_allowances SET reserved_cost=reserved_cost+? WHERE day=? AND kind='planner'",
                         (extra, day))
        if refresh_request:
            from .evidence_refresh import enqueue_refresh
            enqueue_refresh(conn, refresh_request['company_id'], 'research_gap', session['session_id'],
                            at=at, parent_session_id=session['session_id'])
        number = conn.execute('SELECT COALESCE(MAX(step_number),-1)+1 FROM research_steps WHERE session_id=?',
                              (session['session_id'],)).fetchone()[0]
        event = {**event, 'at': at, 'step_number': number}
        conn.execute('INSERT INTO research_steps VALUES (?,?,?,?)',
                     (session['session_id'], number, digest(event), canonical(event)))
        _write(conn, 'research_sessions', session)
        conn.execute('UPDATE research_sessions SET status=?,final_package_id=? WHERE session_id=?',
                     (session['status'], session.get('final_package_id'), session['session_id']))
        _write(conn, 'research_session_progress', progress)


def replay_research_session(session_id, *, db_path=DB_PATH):
    """Read the recorded trace only. Never invoke a planner, adapter or current evidence lookup."""
    with closing(get_connection(db_path)) as conn:
        session = read_record(conn, 'research_sessions', 'session_id', session_id)
        progress = read_record(conn, 'research_session_progress', 'session_id', session_id)
        steps = []
        for row in conn.execute('SELECT * FROM research_steps WHERE session_id=? ORDER BY step_number', (session_id,)):
            step = json.loads(row['record_json'])
            if digest(step) != row['record_hash'] or step['step_number'] != len(steps):
                raise ValueError('Corrupt research journal')
            steps.append(step)
        return {'session': session, 'initial_input': progress['initial_input'], 'steps': steps,
                'evidence': progress['evidence'], 'unresolved_questions': progress['unresolved'],
                'shadow': True}


def pending_research_candidates(*, limit, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute('''SELECT payload_json,payload_hash,created_at FROM (
            SELECT c.payload_json,c.payload_hash,c.created_at FROM discovery_shadow_candidates c
            LEFT JOIN research_sessions s ON s.queue_id=c.queue_id
                AND s.session_id NOT IN (SELECT session_id FROM research_evidence_continuations)
            WHERE (s.session_id IS NULL OR s.status IN ('pending','running'))
                AND COALESCE(json_extract(c.payload_json,'$.research_reused'),0)=0
            UNION ALL
            SELECT c.payload_json,c.payload_hash,c.created_at FROM research_evidence_continuations c
            LEFT JOIN research_sessions s ON s.session_id=c.session_id
            WHERE s.session_id IS NULL OR s.status IN ('pending','running'))
            ORDER BY created_at,payload_hash LIMIT ?''', (limit,)).fetchall()
        results = []
        for row in rows:
            payload = json.loads(row['payload_json'])
            if digest(payload) != row['payload_hash']:
                raise ValueError('Corrupt shadow research input')
            results.append(payload)
        return results


def research_event_superseded(queue_id, *, session_id=None, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        item = read_record(conn, 'discovery_queue', 'queue_id', queue_id)
        latest = conn.execute('''SELECT evidence_revision,payload_json,payload_hash FROM discovery_events
            WHERE company_id=? AND catalyst_id=? ORDER BY observed_at DESC,rowid DESC LIMIT 1''',
                              (item['company_id'], item['catalyst_id'])).fetchone()
        if latest is None or latest['evidence_revision'] == item['evidence_revision']:
            return False
        if session_id:
            from .research_work import session_covers_event
            event = json.loads(latest['payload_json'])
            if digest(event) != latest['payload_hash']:
                raise ValueError('Corrupt discovery event')
            if session_covers_event(conn, session_id, event, item['purpose']):
                return False
        return True
