"""Promotion proofs are bound to policy values and implementation sources."""
from dataclasses import asdict, is_dataclass, replace
from contextlib import closing
from hashlib import sha256
import logging
from pathlib import Path
import sqlite3

from db_common import database_path
from services.research.research_contract import fingerprint


ROOT = Path(__file__).resolve().parents[3]
MODE_KEYS = {'mode', 'enforcement_mode', 'provenance_mode', 'freshness_mode',
             'liquidity_mode', 'scenario_mode', 'cluster_mode', 'exit_mode'}
GATE_BLOCKS = {'PROVENANCE': 'catalyst', 'FRESHNESS': 'catalyst', 'CONTINUATION': 'continuation',
               'REGIME': 'regime', 'LIQUIDITY': 'contracts', 'RISK_CLUSTER': 'contracts',
               'EXECUTION_LIQUIDITY': 'contracts',
               'SCENARIO_SELECTION': 'contracts', 'POSITION_EXIT_V2': 'contracts',
               'EVIDENCE_THESIS': 'thesis', 'TRADE_ECONOMICS': 'economics',
               'TRADE_ECONOMICS_SUBMISSION': 'economics'}


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


def approved(stage, policy, *, db_path=None, code_hash=None):
    """`code_hash` lets a caller checking several stages hash the sources once."""
    try:
        with closing(sqlite3.connect(f'file:{Path(database_path(db_path)).resolve()}?mode=ro', uri=True)) as conn:
            row = conn.execute('SELECT 1 FROM research_gate_promotions WHERE stage=? AND policy_hash=? AND gate_value>0 AND sample_size>=100',
                               (stage, policy_hash(stage, policy, code_hash or source_hash()))).fetchone()
            return row is not None
    except sqlite3.Error:
        return False


def guard_policy(policy, stages, *, db_path=None):
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


# Discovery intake and paired forward evaluations are research records with no capital path.
PAIRED_PREFIX = 'paired:'
RESEARCH_ONLY_PREFIXES = ('discovery:', PAIRED_PREFIX)


def research_only_candidate(candidate_id):
    return str(candidate_id or '').startswith(RESEARCH_ONLY_PREFIXES)


# Not part of what a champion was tested on. Source hashes change on every deploy; the
# top-level version and these digests are computed over modes or code. Modes are excluded
# for the same reason policy_hash excludes them: each gate's enforcement has its own proof,
# so promoting a gate must not revoke the champion that a deploy did not change.
CHAMPION_EXCLUDED_KEYS = frozenset({'source_hashes', 'pipeline_version', 'gate_config_hash',
                                    'provider_config_hash', 'research_budget_hash',
                                    'economics_assumptions_hash', 'evaluator_hash'}) | MODE_KEYS
CHAMPION_MISMATCH_PATHS = 20


def champion_identity(manifest):
    """The decision configuration a champion is bound to: prompts, models and thresholds."""
    if isinstance(manifest, dict):
        return {key: champion_identity(value) for key, value in manifest.items() if key not in CHAMPION_EXCLUDED_KEYS}
    if isinstance(manifest, list):
        return [champion_identity(value) for value in manifest]
    return manifest


def _differing_paths(expected, actual, prefix=''):
    if isinstance(expected, dict) and isinstance(actual, dict):
        return [path for key in sorted(set(expected) | set(actual), key=str)
                for path in _differing_paths(expected.get(key), actual.get(key), f'{prefix}.{key}' if prefix else str(key))]
    return [] if expected == actual else [prefix or '<root>']


def champion_check(candidate, *, db_path=None):
    """(allowed, reason, differing manifest paths). Research candidates never deploy."""
    context = candidate.get('candidate_context') or {}
    if (candidate.get('research_run_id') or candidate.get('research_strategy')
            or research_only_candidate(candidate.get('candidate_id') or context.get('candidate_id'))):
        return False, 'research_candidate', []
    try:
        with closing(sqlite3.connect(f'file:{Path(database_path(db_path)).resolve()}?mode=ro', uri=True)) as conn:
            row = conn.execute('''SELECT r.manifest_json FROM research_champion c
                LEFT JOIN research_runs r ON r.run_id=c.run_id WHERE c.singleton=1''').fetchone()
    except sqlite3.OperationalError as exc:
        # Pre-migration installations continue the existing single production path.
        # Other DB failures cannot grant capital authority.
        allowed = 'no such table' in str(exc)
        return allowed, 'no_champion_table' if allowed else 'champion_registry_unavailable', []
    if row is None or row[0] is None:
        return True, 'no_champion', []
    import json
    expected = champion_identity(json.loads(row[0]))
    actual = champion_identity(candidate.get('execution_version'))
    if expected == actual:
        return True, 'champion_manifest', []
    return False, 'champion_manifest_mismatch', _differing_paths(expected, actual)[:CHAMPION_MISMATCH_PATHS]


def champion_allows(candidate, *, db_path=None):
    """Research candidates never deploy; a promoted manifest owns the capital path."""
    return champion_check(candidate, db_path=db_path)[0]
