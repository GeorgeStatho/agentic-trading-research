"""Promotion proofs are bound to policy values and implementation sources."""
from dataclasses import asdict, is_dataclass, replace
from contextlib import closing
from hashlib import sha256
import logging
from pathlib import Path
import sqlite3

from db_common import DB_PATH
from services.research_contract import fingerprint


ROOT = Path(__file__).resolve().parents[2]
MODE_KEYS = {'mode', 'enforcement_mode', 'provenance_mode', 'freshness_mode',
             'liquidity_mode', 'scenario_mode', 'cluster_mode', 'exit_mode'}
GATE_BLOCKS = {'PROVENANCE': 'catalyst', 'FRESHNESS': 'catalyst', 'CONTINUATION': 'continuation',
               'REGIME': 'regime', 'LIQUIDITY': 'contracts', 'RISK_CLUSTER': 'contracts',
               'EXECUTION_LIQUIDITY': 'contracts',
               'SCENARIO_SELECTION': 'contracts', 'POSITION_EXIT_V2': 'contracts'}


def source_hash():
    paths = []
    for folder in ('Python Scripts/services', 'Python Scripts/agentCallers', 'Python Scripts/trading_support',
                   'Python Scripts/Webscraping/processing', 'Data/market_db_modules', 'Data/news_db_modules'):
        paths.extend((ROOT / folder).rglob('*.py'))
    paths.extend((ROOT / 'Data/market_db_modules').glob('*.sql'))
    paths.extend(ROOT / name for name in ('config/risk_clusters.csv', 'config/source.json',
                                        'Data/catalyst_models.py', 'Data/catalyst_schema.sql', 'Data/sector_etfs.json',
                                        'Data/research_foundation_contracts.py'))
    paths.extend((ROOT / 'Python Scripts/Webscraping/company_evidence').rglob('*.py'))
    return fingerprint({str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest() for p in sorted(paths)})


def policy_hash(stage, policy, code_hash):
    values = asdict(policy) if is_dataclass(policy) else dict(policy)
    values = {key: sorted(value) if isinstance(value, (set, frozenset)) else value
              for key, value in values.items() if key not in MODE_KEYS}
    return fingerprint({'stage': stage, 'policy': values, 'code_hash': code_hash})


def approved(stage, policy, *, db_path=DB_PATH, code_hash=None):
    """`code_hash` lets a caller checking several stages hash the sources once."""
    try:
        with closing(sqlite3.connect(f'file:{Path(db_path).resolve()}?mode=ro', uri=True)) as conn:
            row = conn.execute('SELECT 1 FROM research_gate_promotions WHERE stage=? AND policy_hash=? AND gate_value>0 AND sample_size>=100',
                               (stage, policy_hash(stage, policy, code_hash or source_hash()))).fetchone()
            return row is not None
    except sqlite3.Error:
        return False


def guard_policy(policy, stages, *, db_path=DB_PATH):
    enforcing = {field: (required,) if isinstance(required, str) else required
                 for field, required in stages.items() if getattr(policy, field) == 'enforce'}
    if not enforcing:
        return policy
    code_hash = source_hash()
    changes = {}
    for field, required in enforcing.items():
        if not all(approved(stage, policy, db_path=db_path, code_hash=code_hash) for stage in required):
            logging.getLogger(__name__).warning('%s stays shadow: no matching counterfactual promotion proof', ', '.join(required))
            changes[field] = 'shadow'
    return replace(policy, **changes) if changes else policy


def champion_allows(candidate, *, db_path=DB_PATH):
    """Research candidates never deploy; a promoted manifest owns the capital path."""
    if candidate.get('research_run_id') or candidate.get('research_strategy'):
        return False
    try:
        with closing(sqlite3.connect(f'file:{Path(db_path).resolve()}?mode=ro', uri=True)) as conn:
            row = conn.execute('''SELECT r.manifest_json FROM research_champion c
                LEFT JOIN research_runs r ON r.run_id=c.run_id WHERE c.singleton=1''').fetchone()
            if row is None or row[0] is None:
                return True
            import json
            return json.loads(row[0]) == candidate.get('execution_version')
    except sqlite3.OperationalError as exc:
        # Pre-migration installations continue the existing single production path.
        # Other DB failures cannot grant capital authority.
        return 'no such table' in str(exc)
