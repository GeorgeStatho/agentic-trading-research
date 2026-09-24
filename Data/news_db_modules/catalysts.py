"""First-class catalyst state derived from persisted articles, never model-invented roots."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json

from catalyst_models import CatalystState, HALF_LIFE_MINUTES, PRIMARY_SOURCE_TYPES, classify_catalyst, timestamp
from db_common import DB_PATH, get_connection, json_text


def _earliest(rows, key):
    values = [timestamp(row.get(key)) for row in rows]
    return min((value for value in values if value), default=None)


def _iso(value):
    return value.isoformat() if value else None


def build_catalyst_state(catalyst_id, articles, *, ticker=None, as_of=None):
    articles = sorted(articles, key=lambda row: (timestamp(row.get('event_time')) or datetime.max.replace(tzinfo=timezone.utc), row['id']))
    anchor = articles[0] if articles else {}
    classification = classify_catalyst(anchor.get('title'), anchor.get('summary'))
    roots = {}
    for row in articles:
        if row.get('root_event_id') and row.get('root_source_id') not in {None, '', 'unverified'}:
            roots.setdefault(row['root_event_id'], []).append(row)
    root_times = [min((value for value in (_earliest(rows, 'event_time'), _earliest(rows, 'root_first_public_time')) if value), default=None)
                  for rows in roots.values()]
    from . import shared  # Establish the existing scraping-module import path.
    from processing.scoring import confirmation_score, compute_evidence_score
    confirmation = confirmation_score(len(roots) or (1 if articles else 0))
    scores = [compute_evidence_score(recency=row.get('recency_score') or 0,
              source_reputation=row.get('source_reputation_score') or 0, directness=row.get('directness_score') or 0,
              confirmation=confirmation, factuality=row.get('factuality_score') or 0) for row in articles]
    state = CatalystState(
        catalyst_id=catalyst_id, ticker=ticker or anchor.get('primary_ticker'), **classification,
        first_public_time=_iso(min((value for value in (_earliest(articles, 'event_time'),
                         _earliest(articles, 'root_first_public_time')) if value), default=None)),
        first_seen_time=_iso(_earliest(articles, 'first_seen_time')),
        latest_evidence_time=_iso(max((timestamp(row.get('event_time')) for row in articles if timestamp(row.get('event_time'))), default=None)),
        latest_root_event_time=_iso(max((value for value in root_times if value), default=None)),
        article_count=len(articles), source_count=len({row.get('root_source_id') for row in articles if row.get('root_source_id')}),
        independent_root_count=len(roots), primary_source_present=any(row.get('source_type') in PRIMARY_SOURCE_TYPES for row in articles),
        evidence_score=max(scores) if scores else None, root_event_ids=sorted(roots),
        expected_half_life_minutes=HALF_LIFE_MINUTES.get(classification['catalyst_type']))
    values = asdict(state)
    first = timestamp(state.first_public_time)
    now = timestamp(as_of) if as_of is not None else datetime.now(timezone.utc)
    values['catalyst_age_minutes'] = (now-first).total_seconds()/60 if first and now and first <= now else None
    return values


def store_catalyst_state(state, *, conn):
    columns = [row['name'] for row in conn.execute('PRAGMA table_info(catalysts)')
               if row['name'] not in {'state_json', 'updated_at'}]
    values = {key: state.get(key) for key in columns}
    values.update(state_json=json_text(state), updated_at=datetime.now(timezone.utc).isoformat())
    conn.execute('INSERT INTO catalysts ('+', '.join(values)+') VALUES ('+', '.join('?' for _ in values)+') '
                 'ON CONFLICT(catalyst_id) DO UPDATE SET '+', '.join(f'{key}=excluded.{key}' for key in values if key != 'catalyst_id'),
                 tuple(values.values()))


def refresh_catalyst(catalyst_id, *, conn):
    rows = [dict(row) for row in conn.execute('''SELECT a.*, r.first_public_time AS root_first_public_time
        FROM news_articles a LEFT JOIN root_events r ON r.root_event_id=a.root_event_id WHERE a.catalyst_id=?''', (catalyst_id,))]
    state = build_catalyst_state(catalyst_id, rows)
    if state['ticker'] and conn.execute("SELECT 1 FROM sqlite_master WHERE name='companies'").fetchone():
        company = conn.execute('''SELECT s.sector_key, i.industry_key FROM companies c
            JOIN industries i ON i.id=c.industry_id JOIN sectors s ON s.id=i.sector_id WHERE c.symbol=?''',
            (state['ticker'],)).fetchone()
        if company:
            state.update(sector=company['sector_key'], industry=company['industry_key'])
    store_catalyst_state(state, conn=conn)
    conn.execute('DELETE FROM root_events WHERE catalyst_id=?', (catalyst_id,))
    for root in sorted({row['root_event_id'] for row in rows if row.get('root_event_id')}):
        members = [row for row in rows if row['root_event_id'] == root]
        conn.execute('''INSERT INTO root_events VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(root_event_id) DO UPDATE SET catalyst_id=excluded.catalyst_id,
            first_public_time=excluded.first_public_time, first_seen_time=excluded.first_seen_time,
            verified=excluded.verified, primary_source_present=excluded.primary_source_present''',
            (root, catalyst_id, members[0]['root_source_id'], _iso(min((value for value in (
                 _earliest(members, 'event_time'), _earliest(members, 'root_first_public_time')) if value), default=None)),
             _iso(_earliest(members, 'first_seen_time')), members[0]['root_source_id'] != 'unverified',
             any(row['source_type'] in PRIMARY_SOURCE_TYPES for row in members)))
    return state


def attach_candidate_catalysts(candidate_id, symbol, evidence, *, cutoff, conn):
    from .provenance import update_article_provenance
    from catalyst_schema import stamp_candidate_catalyst
    ids = set()
    has_news = conn.execute("SELECT 1 FROM sqlite_master WHERE name='news_articles'").fetchone()
    for item in evidence:
        article_id = item.get('article_id', item.get('id'))
        row = conn.execute('SELECT * FROM news_articles WHERE id=?', (article_id,)).fetchone() if has_news else None
        if row is None:
            continue
        if not row['catalyst_id'] or not conn.execute('SELECT 1 FROM catalysts WHERE catalyst_id=?', (row['catalyst_id'],)).fetchone():
            update_article_provenance(article_id, conn=conn)
            row = conn.execute('SELECT * FROM news_articles WHERE id=?', (article_id,)).fetchone()
        ids.add(row['catalyst_id'])
    states = []
    for catalyst in sorted(ids):
        rows = [dict(row) for row in conn.execute('''SELECT a.*, r.first_public_time AS root_first_public_time FROM news_articles a
            LEFT JOIN root_events r ON r.root_event_id=a.root_event_id WHERE a.catalyst_id=?
            AND julianday(COALESCE(a.first_seen_time, a.created_at))<=julianday(?)
            AND julianday(a.event_time)<=julianday(?)''', (catalyst, cutoff, cutoff))]
        if rows:
            state = build_catalyst_state(catalyst, rows, as_of=cutoff)
            state.update(candidate_ticker=symbol, relation='direct' if state['ticker'] == symbol else 'readthrough')
            stored = json.loads(conn.execute('SELECT state_json FROM catalysts WHERE catalyst_id=?', (catalyst,)).fetchone()[0])
            state.update(sector=stored.get('sector'), industry=stored.get('industry'))
            states.append(state)
    if not states:
        unknown = build_catalyst_state('catalyst:unknown:'+candidate_id, [], ticker=symbol)
        store_catalyst_state(unknown, conn=conn)
        states = [unknown]
    # Preserve company-level execution: select one explicit primary catalyst and retain all associations.
    states.sort(key=lambda row: (-(row['evidence_score'] or 0), row['catalyst_id']))
    for index, state in enumerate(states):
        conn.execute('INSERT INTO candidate_catalysts VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING',
                     (candidate_id, state['catalyst_id'], state['catalyst_family'], index == 0, json_text(state)))
    stamp_candidate_catalyst(conn, 'candidates', candidate_id, candidate_id)
    conn.execute('UPDATE candidates SET catalyst_ids_json=? WHERE candidate_id=?',
                 (json_text([state['catalyst_id'] for state in states]), candidate_id))
    return states


def enrich_impact(impact, article_id, *, conn):
    row = conn.execute('SELECT * FROM news_articles WHERE id=?', (article_id,)).fetchone()
    if not row:
        raise ValueError('Impact must reference a persisted article')
    if not row['catalyst_id'] or not conn.execute('SELECT 1 FROM catalysts WHERE catalyst_id=?', (row['catalyst_id'],)).fetchone():
        from .provenance import update_article_provenance
        update_article_provenance(article_id, conn=conn)
        row = conn.execute('SELECT * FROM news_articles WHERE id=?', (article_id,)).fetchone()
    requested = impact.get('root_event_ids')
    if requested is not None and (not isinstance(requested, list) or any(
            not isinstance(root, str) or not root.startswith('root:') or root != row['root_event_id'] for root in requested)):
        raise ValueError('root_event_ids must be provenance roots of the cited article, not article IDs')
    state = json.loads(conn.execute('SELECT state_json FROM catalysts WHERE catalyst_id=?', (row['catalyst_id'],)).fetchone()[0])
    return {**impact, **{key: state[key] for key in ('catalyst_id', 'catalyst_family', 'catalyst_type', 'mechanism', 'expected_horizon')},
            'root_event_ids': [row['root_event_id']]}


def backfill_catalysts(*, db_path=DB_PATH):
    from .provenance import backfill_news_provenance
    from catalyst_schema import IMPACT_TABLES
    report = backfill_news_provenance(db_path=db_path)
    count = 0
    with get_connection(db_path) as conn:
        for table in IMPACT_TABLES:
            for row in conn.execute(f'SELECT id, article_id, raw_json FROM {table}').fetchall():
                raw = json.loads(row['raw_json'] or '{}')
                # Legacy payloads retain their original fields; provenance is rebuilt from the article.
                raw = raw if isinstance(raw, dict) else {}
                raw.pop('root_event_ids', None)
                data = enrich_impact(raw, row['article_id'], conn=conn)
                conn.execute(f'''UPDATE {table} SET catalyst_id=?, catalyst_family=?, catalyst_type=?,
                    mechanism=?, root_event_ids_json=?, expected_horizon=?, raw_json=? WHERE id=?''',
                    (data['catalyst_id'], data['catalyst_family'], data['catalyst_type'], data['mechanism'],
                     json_text(data['root_event_ids']), data['expected_horizon'], json_text(data), row['id']))
                count += 1
        report['catalysts'] = conn.execute('SELECT COUNT(*) FROM catalysts WHERE article_count>0').fetchone()[0]
    report['impacts_enriched'] = count
    return report


def catalyst_family_report(*, db_path=DB_PATH):
    from .bootstrap import initialize_database
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        return [dict(row) for row in conn.execute('''SELECT catalyst_family, COUNT(*) AS catalysts,
            SUM(article_count) AS articles, SUM(independent_root_count) AS independent_roots,
            SUM(CASE WHEN classification_status='needs_review' THEN 1 ELSE 0 END) AS needs_review
            FROM catalysts WHERE article_count>0 GROUP BY catalyst_family ORDER BY catalyst_family''')]
