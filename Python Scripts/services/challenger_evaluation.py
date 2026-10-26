"""Shadow incumbent and challenger evaluations of frozen decision observations. No orders, ever.

For each observation the incumbent's recorded model outputs and a challenger's fresh ones, made
from the identical frozen payloads, are finalized by the same deterministic code. A stage whose
model is unchanged reuses the incumbent's output; a changed stage is called again, never replayed.
"""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
import logging
import time

from db_common import get_connection
from market_db_modules.candidate_lifecycle import stage_payloads
from market_db_modules.forward_evaluation import (
    FINAL_EVALUATIONS, cohort_registrations, create_paired_candidate, paired_candidate_id, paired_evaluation,
    pending_paired_observations, reserve_challenger_calls, update_paired_evaluation,
)
from market_db_modules.foundation_store import digest
from market_db_modules.research_observations import get_research_observation
from services.decision_observations import manager_input_payload
from services.forward_evaluation_policy import ChallengerPolicy
from services.paired_decisions import DIRECTIONAL, finalize_paired_decision

LOGGER = logging.getLogger(__name__)
MAX_ATTEMPTS = 2
INCUMBENT_STAGES = ('post_strategist', 'pre_manager', 'pre_gates')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class EvaluationUnavailable(Exception):
    """A permanent reason this observation cannot be evaluated faithfully."""


class ImplementationIdentity:
    """Replays run today's code; they must be the code and settings the decision recorded."""

    def __init__(self):
        self._current = None

    def current(self):
        if self._current is None:
            from agent_stages.manager_runner import MANAGER_PROMPT_VERSION
            from agent_stages.strategist_runner import strategist_prompt_version
            from services.evidence_thesis_policy import EvidenceThesisPolicy
            from services.pipeline_version import gate_config, pipeline_source_hashes, selector_constants
            self._current = {'source_hashes': pipeline_source_hashes(), 'gate_config_hash': digest(gate_config()),
                             'selector_config': selector_constants(),
                             'strategist_prompt_version': strategist_prompt_version(EvidenceThesisPolicy.from_env()),
                             'manager_prompt_version': MANAGER_PROMPT_VERSION}
        return self._current

    def mismatch(self, manifest):
        current = self.current()
        if manifest.get('source_hashes') != current['source_hashes']:
            return 'implementation_changed'
        if (manifest.get('gate_config_hash') != current['gate_config_hash']
                or manifest.get('selector_config') != current['selector_config']):
            return 'configuration_changed'
        if any(manifest.get(key) != current[key] for key in ('strategist_prompt_version', 'manager_prompt_version')):
            return 'prompt_changed'
        return None


def incumbent_outputs(candidate_id, source, *, db_path=None):
    """What the production models actually said, placed on the observation's shared inputs."""
    payloads = stage_payloads(candidate_id, INCUMBENT_STAGES, db_path=db_path)
    if set(payloads) != set(INCUMBENT_STAGES):
        raise EvaluationUnavailable('incumbent_outputs_unavailable')
    manager_payload = manager_input_payload(source)
    manager_payload['strategist_recommendation'] = payloads['pre_manager']['payload'].get('strategist_recommendation') or {}
    return {'strategist_recommendation': payloads['post_strategist'].get('recommendation') or {},
            'recommendation': payloads['pre_gates']['recommendation'], 'manager_payload': manager_payload,
            'model': payloads['pre_manager'].get('model')}


def changed_stages(incumbent_manifest, challenger_manifest):
    """The manager is called again whenever its strategist input changes."""
    strategist = incumbent_manifest['model_names']['strategist'] != challenger_manifest['model_names']['strategist']
    manager = strategist or incumbent_manifest['model_names']['manager'] != challenger_manifest['model_names']['manager']
    return {'strategist': strategist, 'manager': manager}


class Evaluator:
    def __init__(self, *, policy, strategist, manager, persist_check, identity, clock, db_path):
        self.policy, self.strategist, self.manager = policy, strategist, manager
        self.persist_check, self.identity, self.clock, self.db_path = persist_check, identity, clock, db_path

    def run(self, decision, registrations, observation):
        results = {}
        for role in ('incumbent', 'challenger'):
            existing = paired_evaluation(observation['observation_id'], role, db_path=self.db_path)
            if existing and existing['status'] in FINAL_EVALUATIONS:
                results.setdefault('_' + role, existing['status'])
                continue
            if role == 'challenger' and 'unavailable' in (results.get('incumbent'), results.get('_incumbent')):
                # The pair can never resolve, so spend no model calls on it.
                registration = registrations['challenger']
                results[role] = self.finish(observation, role, {'cohort_id': registration['cohort_id'],
                                                                'run_id': registration['run_id'], 'db_path': self.db_path},
                                            'unavailable', 'incumbent_unavailable')
                continue
            results[role] = self.evaluate(role, decision, registrations, observation, existing)
            if results[role] == 'daily_call_limit':
                break
        return {role: status for role, status in results.items() if not role.startswith('_')}

    def evaluate(self, role, decision, registrations, observation, existing):
        registration = registrations.get(role)
        if registration is None:
            raise ValueError('Cohort is missing its ' + role + ' run')
        record = {'cohort_id': registration['cohort_id'], 'run_id': registration['run_id'], 'db_path': self.db_path}
        try:
            manifests = self.manifests(registrations)
            self.require_evaluable(registration, manifests[role], observation, existing)
            started = time.monotonic()
            outputs, calls = self.outputs(role, decision, observation, manifests, registrations)
        except EvaluationUnavailable as exc:
            return self.finish(observation, role, record, 'unavailable', str(exc))
        except DailyCallLimit:
            return 'daily_call_limit'
        except Exception as exc:
            LOGGER.warning('Paired %s evaluation failed for %s: %s', role, observation['observation_id'], exc)
            attempts = (existing or {}).get('attempts', 0) + 1
            status = 'unavailable' if attempts >= MAX_ATTEMPTS else 'pending'
            update_paired_evaluation(observation['observation_id'], role, status=status, at=self.clock(),
                                     reason=f'evaluation_error:{type(exc).__name__}', attempt=True,
                                     model_calls=getattr(exc, 'model_calls', 0), **record)
            return status
        latency = time.monotonic() - started
        context = {**create_paired_candidate(observation, role=role, run_id=registration['run_id'],
                                             manifest=manifests[role], at=self.clock(), db_path=self.db_path),
                   'db_path': self.db_path}
        update_paired_evaluation(observation['observation_id'], role, status='pending', at=self.clock(),
                                 candidate_id=context['candidate_id'], reason='finalizing', model_calls=calls,
                                 latency_seconds=latency, **record)
        try:
            payload = finalize_paired_decision(context, observation, outputs['manager_payload'],
                                               outputs['recommendation'],
                                               strategist_recommendation=outputs['strategist_recommendation'],
                                               model=outputs['model'], thesis_policy=self.thesis_policy(),
                                               economics_policy=self.economics_policy())
        except Exception as exc:
            LOGGER.warning('Paired %s finalization failed for %s: %s', role, observation['observation_id'], exc)
            return self.finish(observation, role, record, 'unavailable', f'finalization_error:{type(exc).__name__}')
        decided = (payload.get('recommendation') or {}).get('decision') in DIRECTIONAL
        return self.finish(observation, role, record, 'evaluated' if decided else 'abstained', None)

    def finish(self, observation, role, record, status, reason):
        update_paired_evaluation(observation['observation_id'], role, status=status, at=self.clock(), reason=reason,
                                 **record)
        return status

    def manifests(self, registrations):
        with closing(get_connection(self.db_path)) as conn:
            rows = {row['run_id']: row for row in conn.execute(
                f'SELECT run_id, status, manifest_json FROM research_runs WHERE run_id IN '
                f'({",".join("?" for _ in registrations)})', [item['run_id'] for item in registrations.values()])}
        self.run_status = {role: rows[item['run_id']]['status'] for role, item in registrations.items()}
        return {role: json.loads(rows[item['run_id']]['manifest_json']) for role, item in registrations.items()}

    def require_evaluable(self, registration, manifest, observation, existing):
        if self.run_status[registration['role']] != 'open':
            raise EvaluationUnavailable('cohort_sealed')
        mismatch = self.identity.mismatch(manifest)
        if mismatch:
            raise EvaluationUnavailable(mismatch)
        candidate_id = paired_candidate_id(observation['observation_id'], registration['role'], registration['run_id'])
        with closing(get_connection(self.db_path)) as conn:
            created = conn.execute('SELECT 1 FROM candidates WHERE candidate_id=?', (candidate_id,)).fetchone()
        if created or (existing or {}).get('candidate_id'):
            # Interrupted after its candidate was created; a model asked again could answer differently.
            raise EvaluationUnavailable('interrupted_during_evaluation')

    def outputs(self, role, decision, observation, manifests, registrations):
        source = observation['source_snapshot']
        incumbent = incumbent_outputs(decision['candidate_id'], source, db_path=self.db_path)
        if role == 'incumbent':
            return incumbent, 0
        changed = changed_stages(manifests['incumbent'], manifests['challenger'])
        calls = sum(changed.values())
        if calls and not reserve_challenger_calls(calls, limit=self.policy.daily_model_calls, at=self.clock(),
                                                  db_path=self.db_path):
            raise DailyCallLimit()
        models = manifests['challenger']['model_names']
        try:
            strategist_recommendation, visible = incumbent['strategist_recommendation'], None
            if changed['strategist']:
                strategist_recommendation = self.challenger_strategist(source, observation, models['strategist'],
                                                                       registrations['challenger']['run_id'])
            manager_payload = manager_input_payload(source)
            if changed['strategist']:
                from agent_stages.manager_strategist_context import _build_manager_visible_strategist_context
                manager_payload['strategist_recommendation'] = strategist_recommendation
                visible = _build_manager_visible_strategist_context(manager_payload)
            manager_payload['strategist_recommendation'] = visible or incumbent['manager_payload']['strategist_recommendation']
            recommendation = incumbent['recommendation']
            if changed['manager']:
                recommendation = self.manager(manager_payload, model=models['manager'])['recommendation']
        except Exception as exc:
            exc.model_calls = calls
            raise
        return {'strategist_recommendation': strategist_recommendation, 'recommendation': recommendation,
                'manager_payload': manager_payload, 'model': models['manager']}, calls

    def challenger_strategist(self, source, observation, model, run_id):
        replay = self.strategist(source['strategist_input'], model=model, thesis_policy=self.thesis_policy())
        recommendation = replay['recommendation']
        if replay.get('thesis_check'):
            from agent_stages.strategist_runner import attach_thesis_check
            evaluation_id = self.persist_check(
                replay['thesis_check'], company_id=int(source['strategist_input']['company']['company_id']),
                candidate_id=paired_candidate_id(observation['observation_id'], 'challenger', run_id),
                context={'model': model, 'prompt_version': replay['prompt_version'], 'paired_role': 'challenger'},
                db_path=self.db_path)
            recommendation = attach_thesis_check(recommendation, replay['thesis_check'], evaluation_id)
        return recommendation

    @staticmethod
    def thesis_policy():
        from services.evidence_thesis_policy import EvidenceThesisPolicy
        return EvidenceThesisPolicy.from_env()

    @staticmethod
    def economics_policy():
        from services.trade_economics_policy import TradeEconomicsPolicy
        return TradeEconomicsPolicy.from_env()


class DailyCallLimit(Exception):
    pass


def run_challenger_evaluations(*, policy=None, strategist=None, manager=None, persist_check=None, identity=None,
                               clock=utc_now, db_path=None):
    policy = policy or ChallengerPolicy.from_env()
    if not policy.enabled:
        return {'status': 'disabled'}
    if strategist is None:
        from agent_stages.strategist_runner import replay_strategist_decision_from_payload as strategist
    if manager is None:
        from agent_stages.manager_runner import replay_manager_decision_from_payload as manager
    if persist_check is None:
        from services.evidence_thesis import persist_thesis_check as persist_check
    evaluator = Evaluator(policy=policy, strategist=strategist, manager=manager, persist_check=persist_check,
                          identity=identity or ImplementationIdentity(), clock=clock, db_path=db_path)
    outcomes, stopped = Counter(), None
    for decision in pending_paired_observations(policy.challenger_id, limit=policy.evaluations_per_cycle,
                                                db_path=db_path):
        registrations = cohort_registrations(decision['cohort_id'], db_path=db_path)
        observation = get_research_observation(decision['observation_id'], db_path=db_path)
        results = evaluator.run(decision, registrations, observation)
        outcomes.update(f'{role}:{status}' for role, status in results.items())
        if 'daily_call_limit' in results.values():
            stopped = 'daily_call_limit'
            break
    return {'status': 'shadow', 'challenger_id': policy.challenger_id, 'outcomes': dict(outcomes),
            'stopped': stopped, 'authority': 'shadow only: paired evaluations never reach the executor'}
