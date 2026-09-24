"""Shared news/market migration, with no dependency on either facade."""
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_suffix('.sql')
IMPACT_TABLES = ('world_news_sector_impacts', 'us_news_sector_impacts', 'sector_opportunist_impacts',
                 'industry_opportunist_impacts', 'company_opportunist_impacts')
LINK_TABLES = ('candidates', 'strategist_runs', 'manager_decision_history', 'candidate_rejections',
               'option_trade_executions', 'position_theses', 'position_outcomes')


def migrate_catalysts(conn):
    conn.executescript(SCHEMA_PATH.read_text())
    for table in (*IMPACT_TABLES, *LINK_TABLES):
        names = {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}
        columns = {'catalyst_id': 'TEXT', 'catalyst_family': 'TEXT'}
        if table in IMPACT_TABLES:
            columns.update(catalyst_type='TEXT', mechanism='TEXT', root_event_ids_json='TEXT', expected_horizon='TEXT')
        for name, kind in columns.items():
            if names and name not in names:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')


def stamp_candidate_catalyst(conn, table, row_id, candidate_id):
    if table not in LINK_TABLES:
        raise ValueError('Unsupported catalyst link table')
    if not candidate_id:
        return
    primary = conn.execute('SELECT catalyst_id, catalyst_family FROM candidate_catalysts '
                           'WHERE candidate_id=? AND is_primary=1', (candidate_id,)).fetchone()
    if primary:
        key = 'candidate_id' if table == 'candidates' else 'id'
        conn.execute(f'UPDATE {table} SET catalyst_id=?, catalyst_family=? WHERE {key}=?',
                     (primary['catalyst_id'], primary['catalyst_family'], row_id))
