"""Rolling forward cohorts, opened before any decision they can enroll.

Manifests are derived from the one the worker recorded with its candidates. Another process
(the collector, the API) builds a different manifest because its environment differs, so it
could never name the worker's configuration.
"""
import copy

from market_db_modules.foundation_store import digest
from market_db_modules.forward_evaluation import ensure_cohort
from research_foundation_contracts import DECISION_OBSERVATION_PROTOCOL
from services.evaluation.forward_evaluation_policy import AcceptancePolicy, ForwardEvaluationPolicy
from services.research.research_promotion import champion_identity
from services.core.time_utils import utc_now_iso

SELECTION_BASIS = 'observation_bucket_chain'


def paired_manifest(base, *, role, challenger=None):
    """An evaluation manifest: the base pipeline, finalized from the observation's frozen chains.

    A challenger's manifest also swaps the strategist/manager models. It is an evaluation identity,
    not a prediction of the manifest production would build after switching models.
    """
    from agent_helpers.llm_client import _resolve_model_name
    manifest = copy.deepcopy(base)
    manifest.pop('pipeline_version', None)
    comparison = {'role': role, 'base_pipeline_version': base['pipeline_version'], 'selection': SELECTION_BASIS}
    if role == 'challenger':
        models = challenger.models(base['model_names'])
        manifest['model_names'] = {stage: _resolve_model_name(base['model_provider'], model)
                                   for stage, model in models.items()}
        for stage, model in models.items():
            settings = ((manifest.get('process_settings') or {}).get('models') or {}).get(stage)
            if isinstance(settings, dict):
                settings['model'] = model
        comparison['challenger'] = challenger.manifest()
    manifest['research_comparison'] = comparison
    manifest['pipeline_version'] = 'v2:' + digest(manifest)
    return manifest


def ensure_production_cohort(manifest, *, policy=None, acceptance=None, at=None, db_path=None):
    """Called before each production candidate is recorded, so no candidate predates its cohort."""
    policy = policy or ForwardEvaluationPolicy.from_env()
    if not policy.enabled or not manifest or not manifest.get('pipeline_version'):
        return None
    # Keyed on strategy identity, not the source-hashed version, so a code-only deploy keeps the cohort.
    identity = 'identity:' + digest(champion_identity(manifest))
    return ensure_cohort({'production': manifest}, base_pipeline_version=identity,
                         challenger_id=None, acceptance=acceptance or AcceptancePolicy.from_env(),
                         at=at or utc_now_iso(), db_path=db_path)


def ensure_comparison_cohort(base, challenger, *, acceptance=None, at=None, db_path=None):
    """The incumbent and challenger runs open together, with the same window and criteria."""
    return ensure_cohort({'incumbent': paired_manifest(base, role='incumbent'),
                          'challenger': paired_manifest(base, role='challenger', challenger=challenger)},
                         base_pipeline_version=base['pipeline_version'], challenger_id=challenger.challenger_id,
                         acceptance=acceptance or AcceptancePolicy.from_env(), at=at or utc_now_iso(),
                         observation_protocol=DECISION_OBSERVATION_PROTOCOL, db_path=db_path)
