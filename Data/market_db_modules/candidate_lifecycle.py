from __future__ import annotations

from datetime import datetime, timezone
from contextlib import closing
from hashlib import sha256
import json
from uuid import uuid4

from db_common import DB_PATH, get_connection, json_text
from .bootstrap import initialize_database


SNAPSHOT_VERSION = 'candidate-snapshot-v1'


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def record_candidate(symbol, version, *, evidence=None, observation_id=None, db_path=DB_PATH):
    initialize_database(db_path=db_path)
    candidate_id, created_at = str(uuid4()), now_iso()
    observation = None
    with closing(get_connection(db_path)) as conn, conn:
        if observation_id:
            from .research_observations import read_observation
            observation = read_observation(conn, observation_id)
            source_evidence = observation['source_snapshot'].get('evidence', [])
            if evidence is not None and evidence != source_evidence:
                raise ValueError('Candidate evidence differs from its frozen observation')
            evidence = source_evidence
        if evidence is None:
            has_news = conn.execute("SELECT 1 FROM sqlite_master WHERE name='news_articles'").fetchone()
            evidence = [dict(row) for row in conn.execute('''
                SELECT a.* FROM news_articles a JOIN company_news_articles l ON l.article_id=a.id
                JOIN companies c ON c.id=l.company_id WHERE UPPER(c.symbol)=?
                AND julianday(a.published_at) BETWEEN julianday(?) - 14 AND julianday(?)
                ORDER BY a.id
            ''', (symbol.upper(), created_at, created_at))] if has_news else []
        article_ids = sorted({row.get('article_id', row.get('id')) for row in evidence
                              if row.get('article_id', row.get('id')) is not None})
        catalyst_ids = sorted({row['catalyst_id'] for row in evidence if row.get('catalyst_id')})
        conn.execute('INSERT INTO candidates (candidate_id, symbol, created_at, catalyst_ids_json, article_ids_json, pipeline_version_json) VALUES (?, ?, ?, ?, ?, ?)',
                     (candidate_id, symbol.upper(), created_at, json_text(catalyst_ids), json_text(article_ids), json_text(version)))
        if observation:
            states = observation['source_snapshot']['catalysts']
            for index, state in enumerate(states):
                conn.execute('INSERT INTO candidate_catalysts VALUES (?,?,?,?,?)',
                             (candidate_id, state['catalyst_id'], state['catalyst_family'],
                              index == 0, json_text(state)))
            conn.execute('UPDATE candidates SET catalyst_ids_json=? WHERE candidate_id=?',
                         (json_text([state['catalyst_id'] for state in states]), candidate_id))
        else:
            from news_db_modules.catalysts import attach_candidate_catalysts
            states = attach_candidate_catalysts(candidate_id, symbol, evidence, cutoff=created_at, conn=conn)
        catalyst_ids = [state['catalyst_id'] for state in states]
        if observation:
            from .research_observations import bind_candidate_observation
            bind_candidate_observation(conn, candidate_id, observation_id)
    return {'candidate_id': candidate_id, 'symbol': symbol.upper(), 'created_at': created_at,
            'catalyst_ids': catalyst_ids, 'article_ids': article_ids, 'pipeline_version': version,
            'evidence': evidence, 'catalysts': states, 'catalyst_id': states[0]['catalyst_id'],
            'catalyst_family': states[0]['catalyst_family'],
            **({'observation_id': observation_id,
                'observation_cutoff': observation['information_cutoff_time'],
                'source_snapshot': observation['source_snapshot']} if observation else {})}


def record_candidate_snapshot(context, stage, payload, *, cutoff=None, db_path=DB_PATH):
    initialize_database(db_path=db_path)
    captured = now_iso()
    cutoff = cutoff or context.get('observation_cutoff') or captured
    version = context['pipeline_version']
    envelope = {'candidate_id': context['candidate_id'], 'stage': stage, 'snapshot_version': SNAPSHOT_VERSION,
                'information_cutoff_time': cutoff, 'pipeline_version': version, 'payload': payload}
    if context.get('observation_id'):
        envelope['observation_id'] = context['observation_id']
    serialized = json.dumps(envelope, sort_keys=True, separators=(',', ':'), allow_nan=False)
    snapshot_hash = sha256(serialized.encode()).hexdigest()
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('''INSERT INTO candidate_snapshots
            (candidate_id, stage, captured_at, information_cutoff_time, pipeline_version,
             snapshot_version, snapshot_hash, snapshot_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id, stage) DO NOTHING''',
            (context['candidate_id'], stage, captured, cutoff, version['pipeline_version'],
             SNAPSHOT_VERSION, snapshot_hash, serialized))


def record_candidate_rejection(context, result, *, shadow=False, db_path=DB_PATH):
    if result.passed:
        return
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        conn.execute('''INSERT INTO candidate_rejections
            (candidate_id, symbol, stage, reason_code, metrics_json, thresholds_json, config_version, occurred_at, shadow)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING''',
            (context['candidate_id'], context.get('symbol'), result.stage, result.reason_code,
             json_text(result.metrics), json_text(result.thresholds),
             context['pipeline_version']['pipeline_version'], now_iso(), int(shadow)))
        from catalyst_schema import stamp_candidate_catalyst
        rejection = conn.execute('SELECT id FROM candidate_rejections WHERE candidate_id=? AND stage=? AND reason_code=? AND shadow=?',
                                 (context['candidate_id'], result.stage, result.reason_code, int(shadow))).fetchone()
        if rejection:
            stamp_candidate_catalyst(conn, 'candidate_rejections', rejection['id'], context['candidate_id'])


def load_candidate_lifecycle(candidate_id, *, db_path=DB_PATH):
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        snapshots = [dict(row) for row in conn.execute(
            'SELECT * FROM candidate_snapshots WHERE candidate_id=? ORDER BY id', (candidate_id,))]
        for snapshot in snapshots:
            if sha256(snapshot['snapshot_json'].encode()).hexdigest() != snapshot['snapshot_hash']:
                raise ValueError('Candidate snapshot hash mismatch')
            snapshot['snapshot'] = json.loads(snapshot.pop('snapshot_json'))
        return {'candidate_id': candidate_id, 'catalysts': [dict(row) for row in conn.execute(
            'SELECT * FROM candidate_catalysts WHERE candidate_id=? ORDER BY is_primary DESC, catalyst_id', (candidate_id,))], 'snapshots': snapshots, 'rejections': [dict(row) for row in conn.execute(
            'SELECT * FROM candidate_rejections WHERE candidate_id=? ORDER BY id', (candidate_id,))],
            'orders': [dict(row) for row in conn.execute(
                'SELECT * FROM option_trade_executions WHERE candidate_id=? ORDER BY id', (candidate_id,))],
            'positions': [dict(row) for row in conn.execute(
                'SELECT * FROM position_outcomes WHERE candidate_id=? ORDER BY id', (candidate_id,))]}


def candidate_funnel(*, date, db_path=DB_PATH):
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        stages = [dict(row) for row in conn.execute('''
            SELECT s.stage, COUNT(DISTINCT s.candidate_id) AS candidates,
                (SELECT COUNT(DISTINCT r.candidate_id) FROM candidate_rejections r
                 JOIN candidates rc ON rc.candidate_id=r.candidate_id
                 WHERE date(rc.created_at)=? AND r.stage=s.stage AND r.shadow=0) AS rejected
            FROM candidate_snapshots s JOIN candidates c ON c.candidate_id=s.candidate_id
            WHERE date(c.created_at)=? GROUP BY s.stage ORDER BY MIN(s.id)
        ''', (date, date))]
        rejections = [dict(row) for row in conn.execute('''SELECT r.* FROM candidate_rejections r
            JOIN candidates c ON c.candidate_id=r.candidate_id WHERE date(c.created_at)=? ORDER BY r.id''', (date,))]
        for row in rejections:
            row['metrics'] = json.loads(row.pop('metrics_json'))
            row['thresholds'] = json.loads(row.pop('thresholds_json'))
        return {'date': date, 'date_basis': 'UTC candidate creation date', 'stages': stages, 'rejections': rejections}
