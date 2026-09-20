"""Content-addressed, non-secret configuration manifests for live decisions."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
import inspect
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return sha256(canonical_json(value).encode()).hexdigest()


def serializable(value):
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return serializable(asdict(value))
    if isinstance(value, dict):
        return {str(k): serializable(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(serializable(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [serializable(v) for v in value]
    return value


def settings_values(settings):
    if is_dataclass(settings):
        return serializable(settings)
    return {key: serializable(value) for key, value in vars(settings).items()
            if not key.startswith('_') and not callable(value)}


def build_pipeline_version(*, settings=None):
    from services.config import FrontMainSettings, OptionPositionSettings, AgentPipelineSettings
    from services.evidence_continuation_gate import DEFAULT_EVIDENCE_CONTINUATION_POLICY
    from services.market_regime_gate import MarketRegimePolicy
    from services.market_regime import MarketRegimeSnapshotPolicy
    from services.option_dte_buckets import OPTION_DTE_BUCKETS
    from agent_stages.strategist_runner import STRATEGIST_PROMPT_VERSION, DEFAULT_MODEL as strategist_model
    from agent_stages.manager_runner import MANAGER_PROMPT_VERSION, DEFAULT_MODEL as manager_model
    from agent_helpers.deterministic_option_selector import SELECTOR_VERSION, config
    from agent_helpers.shared import _normalize_provider, _resolve_model_name
    from agent_stages.strategist_runner import decide_company_purchase
    from agent_stages.manager_runner import decide_company_option_position

    effective_settings = settings or FrontMainSettings.from_env()
    settings_block = settings_values(effective_settings)
    from services.catalyst_gates import CatalystGatePolicy
    from services.pricing_policy import PricingPolicy, HORIZON_DAYS, RESPONSE_HORIZONS
    from catalyst_models import HALF_LIFE_MINUTES, CLASSIFICATION_VERSION, DECAY_BASIS
    gates = serializable({'pricing': PricingPolicy.from_env(), 'pricing_horizons': HORIZON_DAYS,
                          'response_horizons': RESPONSE_HORIZONS,
                          'catalyst_decay_priors': HALF_LIFE_MINUTES, 'catalyst': CatalystGatePolicy.from_env(), 'continuation': DEFAULT_EVIDENCE_CONTINUATION_POLICY,
                          'regime': MarketRegimePolicy.from_env(),
                          'regime_snapshot': MarketRegimeSnapshotPolicy.from_env()})
    def constants(module):
        return {k: serializable(v) for k, v in vars(module).items()
                if k.isupper() and isinstance(v, (str, int, float, bool, dict, list, tuple, set, frozenset))}
    source_files = [p for folder in ('services', 'trading_support', 'agentCallers')
                    for p in (ROOT / 'Python Scripts' / folder).rglob('*.py')]
    source_files.extend([ROOT / path for path in ('Data/catalyst_models.py', 'Data/catalyst_schema.py',
        'Data/catalyst_schema.sql', 'Data/news_db_modules/provenance.py', 'Data/news_db_modules/catalysts.py',
        'Data/market_db_modules/pricing_research.py', 'Data/market_db_modules/pricing_schema.sql',
        'Python Scripts/news_collector_main.py', 'Data/sector_etfs.json', 'config/source.json')])
    source_hashes = {str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest() for p in sorted(source_files)}
    manifest = {
        'manifest_version': 'pipeline-manifest-v1',
        'strategist_prompt_version': STRATEGIST_PROMPT_VERSION,
        'manager_prompt_version': MANAGER_PROMPT_VERSION,
        'model_provider': _normalize_provider(),
        'model_names': {stage: _resolve_model_name(_normalize_provider(), model)
                        for stage, model in (('strategist', strategist_model), ('manager', manager_model))},
        'stage_defaults': {stage: {key: value.default for key, value in inspect.signature(function).parameters.items()
                                  if value.default is None or isinstance(value.default, (str, int, float, bool))}
                           for stage, function in (('strategist', decide_company_purchase), ('manager', decide_company_option_position))},
        'pipeline_settings': settings_values(AgentPipelineSettings.from_env()),
        'catalyst_classification_version': CLASSIFICATION_VERSION,
        'provenance_source_config': json.loads((ROOT / 'config/source.json').read_text()),
        'catalyst_decay_priors': HALF_LIFE_MINUTES, 'catalyst_decay_basis': DECAY_BASIS,
        'gate_config_hash': digest(gates), 'gate_config': gates,
        'runtime_settings': settings_block, 'selector_config': constants(config),
        'contract_selector_version': SELECTOR_VERSION,
        'exit_engine_version': 'option-exits-v1', 'exit_config': serializable(OptionPositionSettings.from_env()),
        'dte_buckets': serializable(OPTION_DTE_BUCKETS), 'source_hashes': source_hashes,
    }
    manifest['pipeline_version'] = 'v2:' + digest(manifest)
    return manifest
