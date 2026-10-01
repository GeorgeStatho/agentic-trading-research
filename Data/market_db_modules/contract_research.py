"""Part 5 persisted mapping, shadow observations and execution readiness."""
from contextlib import closing
import csv
import json
from pathlib import Path

from db_common import DB_PATH, get_connection, json_text

CLUSTER_PATH = Path(__file__).resolve().parents[2] / 'config/risk_clusters.csv'


def migrate_contract_research(conn):
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(companies)')}
    if 'risk_cluster_id' not in columns:
        conn.execute('ALTER TABLE companies ADD COLUMN risk_cluster_id TEXT')
    # Explicit company assignments take precedence over the seed file.
    conn.executemany('UPDATE companies SET risk_cluster_id=? WHERE symbol=? AND risk_cluster_id IS NULL',
                     [(row['risk_cluster_id'], row['symbol']) for row in cluster_mapping_rows()])
    conn.execute('''CREATE TABLE IF NOT EXISTS contract_research_observations (
        observation_id TEXT PRIMARY KEY, candidate_id TEXT, stage TEXT NOT NULL,
        observed_at TEXT NOT NULL, policy_version TEXT NOT NULL, payload_json TEXT NOT NULL)''')


def cluster_mapping_rows():
    with CLUSTER_PATH.open(newline='', encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def get_risk_cluster(symbol, *, db_path=DB_PATH):
    from .bootstrap import initialize_database
    initialize_database(db_path=db_path)
    symbol = str(symbol or '').upper().strip()
    with closing(get_connection(db_path)) as conn, conn:
        row = conn.execute('SELECT risk_cluster_id FROM companies WHERE symbol=?', (symbol,)).fetchone()
    if row and row['risk_cluster_id']:
        return row['risk_cluster_id']
    # Broker-held companies may not yet be present in the news universe.
    return next((row['risk_cluster_id'] for row in cluster_mapping_rows() if row['symbol'] == symbol), None)


def record_observation(observation_id, stage, payload, *, observed_at, candidate_id=None, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('''INSERT INTO contract_research_observations VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(observation_id) DO NOTHING''',
            (observation_id, candidate_id, stage, observed_at, 'contract-execution-v2', json_text(payload)))


def execution_baseline(*, paper=True, regime_state=None, minimum_fills=30, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn, conn:
        rows = [dict(row) for row in conn.execute('''SELECT * FROM option_trade_executions
            WHERE paper=? AND order_side='buy' AND LOWER(order_type)='market' ''', (int(paper),))]
    def regime(row):
        raw = json.loads(row.get('raw_json') or '{}')
        return (raw.get('market_regime_gate') or {}).get('regime_state') or (
            (raw.get('market_context') or {}).get('market_regime') or {}).get('state')
    if regime_state:
        rows = [row for row in rows if regime(row) == regime_state]
    measured = [row for row in rows if (row.get('filled_qty') or 0) > 0 and row.get('slippage_vs_arrival') is not None]
    return {'paper': paper, 'regime_state': regime_state, 'submitted_orders': len(rows),
            'measured_market_fills': len(measured), 'minimum_fills': minimum_fills,
            'baseline_ready': len(measured) >= minimum_fills,
            'mean_slippage_vs_arrival': sum(row['slippage_vs_arrival'] for row in measured) / len(measured) if measured else None,
            'full_fill_rate': sum((row.get('filled_qty') or 0) >= (row.get('order_qty') or float('inf')) for row in rows) / len(rows) if rows else None,
            'would_have_filled_later_samples': sum(row.get('would_have_filled_later') is not None for row in rows),
            'missed_trade_opportunity_pnl': None,
            'live_ladder_ready': False,
            'readiness_note': 'Baseline count is necessary, not promotion approval. R1 option counterfactual comparison remains required.'}


def contract_report(*, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn, conn:
        coverage = [dict(row) for row in conn.execute('SELECT stage, COUNT(*) AS observations FROM contract_research_observations GROUP BY stage')]
        mapping = [dict(row) for row in conn.execute('SELECT risk_cluster_id, COUNT(*) AS companies FROM companies GROUP BY risk_cluster_id')]
    return {'observations': coverage, 'cluster_coverage': mapping,
            'paper_baseline': execution_baseline(db_path=db_path),
            'live_baseline': execution_baseline(paper=False, db_path=db_path)}
