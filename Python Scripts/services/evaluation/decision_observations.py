"""Freeze a production decision's strategy-independent inputs, so a challenger sees exactly the same.

The observation holds what the strategist and manager models were shown, minus anything a
strategy produced, plus every DTE bucket's chain fetched once after the incumbent decided.
Production's own candidate and orders are unchanged; this only records.
"""
import copy
import logging

from market_db_modules.candidate_lifecycle import stage_payloads
from market_db_modules.foundation_store import digest
from market_db_modules.forward_evaluation import record_decision_observation
from market_db_modules.research_observations import record_research_observation
from news_db_modules.catalysts import CATALYST_REVISION_FIELDS
from research_foundation_contracts import DECISION_OBSERVATION_PROTOCOL, ResearchObservation
from services.evaluation.forward_cohorts import ensure_comparison_cohort
from services.research.research_contract import utc
from services.research.research_promotion import research_only_candidate
from services.core.time_utils import utc_now_iso

LOGGER = logging.getLogger(__name__)
DECISION_INPUT_STAGES = ('pre_strategist', 'pre_manager')
# What a strategy produced, never part of the shared inputs.
STRATEGY_OUTPUT_KEYS = ('candidate_id', 'strategist_recommendation')
# The manager receives these from the strategist's frozen evidence; the observation stores them once.
SHARED_EVIDENCE_KEYS = ('filters', 'opportunist_rollup', 'views', 'supporting_articles', 'grounding', 'company_evidence')


def decision_inputs(strategist_payload, manager_snapshot):
    strategist_input = {key: value for key, value in strategist_payload.items() if key not in STRATEGY_OUTPUT_KEYS}
    manager_payload = {key: value for key, value in manager_snapshot['payload'].items()
                       if key not in STRATEGY_OUTPUT_KEYS}
    shared = [key for key in SHARED_EVIDENCE_KEYS
              if key in manager_payload and manager_payload[key] == strategist_input.get(key)]
    for key in shared:
        manager_payload.pop(key)
    return strategist_input, {'payload': manager_payload, 'evidence_from_strategist_input': shared}


def manager_input_payload(source):
    """The manager payload every strategy starts from, before its own strategist context is added."""
    manager = source['manager_input']
    return {**copy.deepcopy(manager['payload']),
            **{key: copy.deepcopy(source['strategist_input'][key]) for key in manager['evidence_from_strategist_input']}}


def observation_cutoff(captured_at, chains):
    """A quote stamped a moment after our own clock was still in hand when the fetch returned."""
    quoted = [utc(contract['latest_quote']['timestamp']) for contracts in chains.get('buckets', {}).values()
              for contract in contracts if (contract.get('latest_quote') or {}).get('timestamp')]
    return max([utc(captured_at), *quoted]).isoformat()


def freeze_decision_observation(candidate_context, *, policy, acceptance=None, fetch_chains=None,
                                per_type_limit=None, clock=utc_now_iso, db_path=None):
    """Record the shared observation for one production decision; never raises into the cycle."""
    candidate_id = (candidate_context or {}).get('candidate_id')
    if policy is None or not policy.enabled or not candidate_id or research_only_candidate(candidate_id):
        return None
    cohort = ensure_comparison_cohort(candidate_context['pipeline_version'], policy, acceptance=acceptance,
                                      at=clock(), db_path=db_path)
    try:
        payloads = stage_payloads(candidate_id, DECISION_INPUT_STAGES, db_path=db_path)
        if set(payloads) != set(DECISION_INPUT_STAGES):
            raise ValueError('decision inputs were not checkpointed')
        strategist_input, manager_input = decision_inputs(payloads['pre_strategist'], payloads['pre_manager'])
        if fetch_chains is None:
            from agent_helpers.market_context.option_chain import build_bucket_chains as fetch_chains
        if per_type_limit is None:
            from services.core.settings import get_settings
            per_type_limit = get_settings().manager_context.option_chain_limit_per_type
        market = manager_input['payload'].get('market_context') or {}
        chains = fetch_chains(candidate_context['symbol'], per_type_limit=per_type_limit,
                              reference_stock_price=(market.get('option_market') or {}).get('reference_stock_price'))
        cutoff = observation_cutoff(clock(), chains)
        states = candidate_context['catalysts']
        primary = states[0]
        observation_id = record_research_observation(ResearchObservation(
            company_id=int(strategist_input['company']['company_id']), symbol=candidate_context['symbol'],
            catalyst_id=primary['catalyst_id'],
            event_revision=digest({field: primary.get(field) for field in CATALYST_REVISION_FIELDS}),
            information_cutoff_time=cutoff, protocol_version=DECISION_OBSERVATION_PROTOCOL,
            source_snapshot={'decision_route': 'production', 'strategist_input': strategist_input,
                             'manager_input': manager_input, 'option_chain': chains, 'catalysts': states,
                             'evidence': [{'article_id': article_id} for article_id in
                                          (strategist_input.get('grounding') or {}).get('article_ids', [])]}),
            db_path=db_path)
        record_decision_observation(candidate_id, observation_id=observation_id, cohort_id=cohort['cohort_id'],
                                    status='recorded', at=cutoff, db_path=db_path)
        return observation_id
    except Exception as exc:
        LOGGER.warning('Decision observation unavailable for %s: %s', candidate_id, exc)
        record_decision_observation(candidate_id, observation_id=None, cohort_id=cohort['cohort_id'],
                                    status='unavailable', reason=f'{type(exc).__name__}: {exc}'[:300],
                                    at=clock(), db_path=db_path)
        return None
