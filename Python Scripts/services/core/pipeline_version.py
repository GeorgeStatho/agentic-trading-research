"""Content-addressed, non-secret configuration manifests for live decisions."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
import inspect
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


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


def gate_config():
    """Decision-relevant policy values; a paired replay refuses to run when they have changed."""
    from services.core.settings import get_settings
    from services.gates.market_regime_gate import MarketRegimePolicy
    from services.gates.market_regime import MarketRegimeSnapshotPolicy
    from services.thesis.evidence_thesis_policy import EvidenceThesisPolicy
    from services.gates.catalyst_gates import CatalystGatePolicy
    from services.gates.pricing_policy import PricingPolicy, HORIZON_DAYS, RESPONSE_HORIZONS
    from services.gates.contract_policy import ContractPolicy
    from catalyst_models import HALF_LIFE_MINUTES
    from services.gates.trade_economics_policy import TradeEconomicsPolicy
    return serializable({'thesis': EvidenceThesisPolicy.from_env(), 'economics': TradeEconomicsPolicy.from_env(),
                         'contracts': ContractPolicy.from_env(), 'pricing': PricingPolicy.from_env(),
                         'pricing_horizons': HORIZON_DAYS, 'response_horizons': RESPONSE_HORIZONS,
                         'catalyst_decay_priors': HALF_LIFE_MINUTES, 'catalyst': CatalystGatePolicy.from_env(),
                         'continuation': get_settings().evidence_continuation,
                         'regime': MarketRegimePolicy.from_env(),
                         'regime_snapshot': MarketRegimeSnapshotPolicy.from_env()})


def selector_constants():
    from agent_helpers.deterministic_option_selector import config
    return {k: serializable(v) for k, v in vars(config).items()
            if k.isupper() and isinstance(v, (str, int, float, bool, dict, list, tuple, set, frozenset))}


def pipeline_source_hashes():
    source_files = [p for folder in ('services', 'trading_support', 'agentCallers')
                    for p in (ROOT / 'Python Scripts' / folder).rglob('*.py')]
    source_files.extend([ROOT / path for path in ('Data/catalyst_models.py', 'Data/catalyst_schema.py',
        'Data/catalyst_schema.sql', 'Data/news_db_modules/provenance.py', 'Data/news_db_modules/catalysts.py',
        'Data/news_db_modules/catalyst_labels.py',
        'Data/market_db_modules/pricing_research.py', 'Data/market_db_modules/pricing_schema.sql',
        'Data/market_db_modules/contract_research.py', 'config/risk_clusters.csv',
        'Data/market_db_modules/trade_economics.py', 'Data/market_db_modules/trade_economics_schema.sql',
        'Data/market_db_modules/thesis_event_reviews.py', 'Data/market_db_modules/thesis_event_reviews_schema.sql',
        'Data/market_db_modules/research_runs.py', 'Data/market_db_modules/research_reports.py',
        'Data/market_db_modules/research_schema.sql',
        'Data/market_db_modules/forward_evaluation.py', 'Data/market_db_modules/forward_evaluation_schema.sql',
        'Data/market_db_modules/forward_comparison.py', 'Data/market_db_modules/discovery_evaluation.py',
        'Data/market_db_modules/research_outcomes.py',
        'Data/market_db_modules/thesis_validator.py',
        'Python Scripts/news_collector_main.py', 'Data/sector_etfs.json', 'config/source.json')])
    return {str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest() for p in sorted(source_files)}


def build_pipeline_version(*, settings=None):
    from services.research.evidence_research_policy import foundation_manifest
    from services.core.trading_config import WorkerSettings, OptionPositionSettings, AgentPipelineSettings
    from services.core.settings import get_settings
    from services.options.option_dte_buckets import OPTION_DTE_BUCKETS
    from agent_stages.strategist_runner import strategist_prompt_version
    from agent_builders.strategist_payload_articles import SUPPORTING_ARTICLE_SELECTION_VERSION
    from services.thesis.evidence_thesis_policy import EvidenceThesisPolicy
    from agent_stages.manager_runner import MANAGER_PROMPT_VERSION
    from agent_helpers.deterministic_option_selector import SELECTOR_VERSION
    from agent_helpers.llm_client import _normalize_provider, _resolve_model_name
    from agent_stages.strategist_runner import decide_company_purchase
    from agent_stages.manager_runner import decide_company_option_position
    from catalyst_models import HALF_LIFE_MINUTES, CLASSIFICATION_VERSION, DECAY_BASIS
    from services.thesis.thesis_event_review_policy import EventThesisReviewPolicy
    from services.evaluation.forward_evaluation_policy import forward_evaluation_manifest, recorded_configuration

    effective_settings = settings or WorkerSettings.from_env()
    process_settings = get_settings()
    settings_block = settings_values(effective_settings)
    thesis_policy = EvidenceThesisPolicy.from_env()
    gates = gate_config()
    manifest = {
        'manifest_version': 'pipeline-manifest-v1',
        'strategist_prompt_version': strategist_prompt_version(thesis_policy),
        'supporting_article_selection_version': SUPPORTING_ARTICLE_SELECTION_VERSION,
        'manager_prompt_version': MANAGER_PROMPT_VERSION,
        'model_provider': _normalize_provider(),
        'model_names': {stage: _resolve_model_name(_normalize_provider(stage_model.provider), stage_model.model)
                        for stage, stage_model in (('strategist', process_settings.models.strategist),
                                                   ('manager', process_settings.models.manager))},
        'stage_defaults': {stage: {key: value.default for key, value in inspect.signature(function).parameters.items()
                                  if value.default is None or isinstance(value.default, (str, int, float, bool))}
                           for stage, function in (('strategist', decide_company_purchase), ('manager', decide_company_option_position))},
        'pipeline_settings': settings_values(AgentPipelineSettings.from_env()),
        'catalyst_classification_version': CLASSIFICATION_VERSION,
        'provenance_source_config': json.loads((ROOT / 'config/source.json').read_text()),
        'catalyst_decay_priors': HALF_LIFE_MINUTES, 'catalyst_decay_basis': DECAY_BASIS,
        'gate_config_hash': digest(gates), 'gate_config': gates,
        'runtime_settings': settings_block, 'selector_config': selector_constants(),
        'contract_selector_version': SELECTOR_VERSION,
        'exit_engine_version': 'option-exits-v2-shadow-priors', 'exit_config': serializable(OptionPositionSettings.from_env()),
        'dte_buckets': serializable(OPTION_DTE_BUCKETS), 'source_hashes': pipeline_source_hashes(),
        'event_thesis_review': recorded_configuration(lambda: EventThesisReviewPolicy.from_env().manifest()),
        'forward_evaluation': forward_evaluation_manifest(),
        'research_foundation': foundation_manifest(),
        # The one settings object this process loaded; see services/core/settings.py.
        'process_settings': serializable(process_settings),
    }
    manifest['pipeline_version'] = 'v2:' + digest(manifest)
    return manifest
