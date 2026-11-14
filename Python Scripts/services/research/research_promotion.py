"""Promotion proofs are bound to policy values and implementation sources."""
from dataclasses import asdict, is_dataclass, replace
from contextlib import closing
import logging
import os
from pathlib import Path
import sqlite3

from db_common import database_path
from services.core.trading_path import trading_path_hashes
from services.research.research_contract import fingerprint


MODE_KEYS = {'mode', 'enforcement_mode', 'provenance_mode', 'freshness_mode',
             'liquidity_mode', 'scenario_mode', 'cluster_mode', 'exit_mode'}
GATE_BLOCKS = {'PROVENANCE': 'catalyst', 'FRESHNESS': 'catalyst', 'CONTINUATION': 'continuation',
               'REGIME': 'regime', 'LIQUIDITY': 'contracts', 'RISK_CLUSTER': 'contracts',
               'EXECUTION_LIQUIDITY': 'contracts',
               'SCENARIO_SELECTION': 'contracts', 'POSITION_EXIT_V2': 'contracts',
               'EVIDENCE_THESIS': 'thesis', 'TRADE_ECONOMICS': 'economics',
               'TRADE_ECONOMICS_SUBMISSION': 'economics', 'ENTRY_EXTENSION': 'entry_extension',
               'REGIME_SPY_TREND': 'regime_spy_trend', 'TRADABILITY': 'tradability'}


_CATALYST_FILES = ('Python Scripts/services/gates/catalyst_gates.py', 'Data/catalyst_models.py',
                   'Python Scripts/Webscraping/processing/scoring.py', 'Data/news_db_modules/catalysts.py',
                   'Data/news_db_modules/provenance.py', 'config/source.json')
_CONTRACT_POLICY = 'Python Scripts/services/gates/contract_policy.py'
_SELECTOR = 'Python Scripts/agentCallers/agent_helpers/deterministic_option_selector/'
# The deterministic selector picks the contract that TRADABILITY and TRADE_ECONOMICS judge.
_SELECTOR_FILES = tuple(_SELECTOR + name for name in ('__init__.py', 'config.py', 'debug.py', 'filters.py', 'greeks.py',
                                                       'hybrid.py', 'market.py', 'normalize.py', 'simple.py', 'volatility.py'))
# The files whose content decides a stage's verdict: its rule and the producers of what it reads.
# A proof survives deploys that leave them unchanged; an unlisted stage binds to the whole trading path.
GATE_FILES = {
    # with_primary_filing reads the issuer's filings through company_evidence.
    'PROVENANCE': (*_CATALYST_FILES, 'Data/market_db_modules/company_evidence.py'),
    'FRESHNESS': _CATALYST_FILES,
    'REGIME': ('Python Scripts/services/gates/market_regime_gate.py', 'Data/catalyst_models.py'),
    'REGIME_SPY_TREND': ('Python Scripts/services/gates/market_regime_variants.py',
                         'Python Scripts/services/gates/market_regime.py', 'Python Scripts/services/core/trade_sides.py'),
    # Its move input comes from the strategist payload's yfinance five-day return.
    'ENTRY_EXTENSION': ('Python Scripts/services/gates/entry_extension_gate.py',
                        'Python Scripts/agentCallers/agent_builders/strategist_payload_price.py',
                        'Python Scripts/services/core/trade_sides.py'),
    # The stock quote and the selector pick the contract; its reference price and the cap resolution decide the fit.
    'TRADABILITY': ('Python Scripts/services/gates/tradability_gate.py', 'Python Scripts/services/options/reference_price.py',
                    'Python Scripts/services/core/trading_config.py', 'Python Scripts/services/options/option_pricing.py',
                    'Python Scripts/services/options/option_dte_buckets.py',
                    'Python Scripts/services/options/scenario_selector.py',
                    'Python Scripts/services/gates/liquidity_gate.py', _CONTRACT_POLICY,
                    'Python Scripts/services/gates/option_hurdle_gate.py',
                    'Python Scripts/agentCallers/agent_helpers/market_context/alpaca_base.py',
                    'Python Scripts/agentCallers/agent_helpers/market_context/equities.py',
                    'Python Scripts/agentCallers/agent_helpers/market_context/option_chain.py',
                    'Python Scripts/agentCallers/agent_helpers/market_context/iv_history.py',
                    'Python Scripts/agentCallers/agent_helpers/volatility.py', *_SELECTOR_FILES),
    # The report prices the selected contract on the family's stored residual responses.
    'TRADE_ECONOMICS': ('Python Scripts/services/gates/trade_economics.py',
                        'Python Scripts/services/gates/trade_economics_policy.py',
                        'Python Scripts/services/gates/pricing_policy.py', 'Python Scripts/services/options/option_pricing.py',
                        'Python Scripts/services/options/option_dte_buckets.py',
                        'Python Scripts/services/runtime/execution_telemetry.py',
                        'Python Scripts/services/discovery/catalyst_response_collector.py',
                        'Data/market_db_modules/pricing_research.py', 'Data/market_db_modules/trade_economics.py',
                        'Data/news_db_modules/catalysts.py', 'Data/catalyst_models.py', *_SELECTOR_FILES),
    'CONTINUATION': ('Python Scripts/services/gates/evidence_continuation_gate.py',
                     'Python Scripts/services/gates/evidence_continuation_policy.py', 'Data/catalyst_models.py'),
    'LIQUIDITY': ('Python Scripts/services/gates/liquidity_gate.py', _CONTRACT_POLICY,
                  'Python Scripts/services/runtime/execution_telemetry.py'),
    'RISK_CLUSTER': ('Python Scripts/services/gates/risk_clusters.py', _CONTRACT_POLICY,
                     'Data/market_db_modules/contract_research.py', 'config/risk_clusters.csv'),
    'EVIDENCE_THESIS': ('Python Scripts/services/thesis/evidence_thesis.py',
                        'Python Scripts/services/thesis/evidence_thesis_checks.py',
                        'Python Scripts/services/thesis/evidence_thesis_policy.py',
                        'Python Scripts/agentCallers/agent_stages/evidence_thesis_contract.py'),
}


def source_hash():
    return fingerprint(trading_path_hashes())


def gate_code_hash(stage, source_hashes):
    """The identity of one stage's code, from a trading-path {file: sha256} map."""
    files = GATE_FILES.get(stage)
    return fingerprint({name: source_hashes.get(name) for name in files}) if files else fingerprint(source_hashes)


def gate_code_hashes(source_hashes):
    return {stage: gate_code_hash(stage, source_hashes) for stage in GATE_BLOCKS}


def policy_hash(stage, policy, code_hash):
    """`code_hash` is the stage's gate_code_hash."""
    values = asdict(policy) if is_dataclass(policy) else dict(policy)
    values = {key: sorted(value) if isinstance(value, (set, frozenset)) else value
              for key, value in values.items() if key not in MODE_KEYS}
    return fingerprint({'stage': stage, 'policy': values, 'code_hash': code_hash})


def approved(stage, policy, *, db_path=None, source_hashes=None):
    """`source_hashes` lets a caller checking several stages hash the sources once."""
    try:
        code = gate_code_hash(stage, source_hashes or trading_path_hashes())
        with closing(sqlite3.connect(f'file:{Path(database_path(db_path)).resolve()}?mode=ro', uri=True)) as conn:
            row = conn.execute('SELECT 1 FROM research_gate_promotions WHERE stage=? AND policy_hash=? AND gate_value>0 AND sample_size>=100',
                               (stage, policy_hash(stage, policy, code))).fetchone()
            return row is not None
    except sqlite3.Error:
        return False


def guard_policy(policy, stages, *, db_path=None):
    enforcing = {field: (required,) if isinstance(required, str) else required
                 for field, required in stages.items() if getattr(policy, field) == 'enforce'}
    if not enforcing:
        return policy
    hashes = trading_path_hashes()
    changes = {}
    for field, required in enforcing.items():
        if not all(approved(stage, policy, db_path=db_path, source_hashes=hashes) for stage in required):
            logging.getLogger(__name__).warning('%s stays shadow: no matching counterfactual promotion proof', ', '.join(required))
            changes[field] = 'shadow'
    return replace(policy, **changes) if changes else policy


def rollout_mode(env_var, modes=('shadow', 'enforce')):
    """A gate's rollout mode from the environment; an unrecognised value stays in shadow."""
    value = str(os.getenv(env_var, 'shadow')).strip().lower()
    return value if value in modes else 'shadow'


# Discovery intake and paired forward evaluations are research records with no capital path.
PAIRED_PREFIX = 'paired:'
RESEARCH_ONLY_PREFIXES = ('discovery:', PAIRED_PREFIX)


def research_only_candidate(candidate_id):
    return str(candidate_id or '').startswith(RESEARCH_ONLY_PREFIXES)


# Not part of what a champion was tested on. Source hashes change on every deploy; the
# top-level version and these digests are computed over modes or code. Modes are excluded
# for the same reason policy_hash excludes them: each gate's enforcement has its own proof,
# so promoting a gate must not revoke the champion that a deploy did not change.
# Follower accounts paper-trade beside production and never change its decisions.
CHAMPION_EXCLUDED_KEYS = frozenset({'source_hashes', 'pipeline_version', 'gate_config_hash',
                                    'provider_config_hash', 'research_budget_hash',
                                    'economics_assumptions_hash', 'evaluator_hash', 'followers'}) | MODE_KEYS
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
