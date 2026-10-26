"""Bounded, resumable shadow investigation. This module has no execution dependency."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import time

from market_db_modules.company_evidence import get_evidence_package, record_evidence_package
from market_db_modules.foundation_store import digest
from market_db_modules.research_foundation import get_discovery_item, get_research_session, record_research_session
from market_db_modules.research_runtime import (
    ResearchBudgetExceeded, initialize_research_progress, load_research_progress,
    pending_research_candidates, research_checkpoint, research_event_superseded,
)
from research_foundation_contracts import EvidencePackage, ResearchSession, timestamp
from services.evidence_research_policy import EvidenceResearchPolicy
from services.research_evidence import (
    coverage_outcome, evidence_identity, final_package, initial_evidence, merge_evidence, required_requests,
)
from services.research_lock import ResearchSessionBusy, research_session_lock
from services.research_model_policy import billed
from services.research_tools import OPERATIONS, ResearchTools
from agent_stages.research_planner import ResearchPlanner
from agent_stages.research_planner import ResearchProviderError
from agent_stages.research_planner import validate_plan
from market_db_modules.bootstrap import initialize_database
from market_db_modules.foundation_store import canonical
from market_db_modules.research_observations import get_research_observation
from research_foundation_contracts import nonnegative
from services.research_continuation import prepare_research_continuations


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _initial_package(candidate, item, db_path):
    observation = get_research_observation(candidate['observation_id'], db_path=db_path)
    cutoff = observation['information_cutoff_time']
    supplied = candidate['research_input']['company_evidence'].get('package_id')
    if supplied:
        package = get_evidence_package(supplied, db_path=db_path)
        if timestamp(package['information_cutoff_time']) > timestamp(cutoff):
            raise ValueError('Initial package is after the frozen candidate cutoff')
        if timestamp(package['information_cutoff_time']) == timestamp(cutoff):
            return package
        fields = {key: value for key, value in package.items() if key != 'package_id'}
        fields['information_cutoff_time'] = cutoff
    else:
        fields = dict(company_id=item['company_id'], catalyst_id=item['catalyst_id'],
                      event_revision=item['evidence_revision'], information_cutoff_time=cutoff,
                      document_ids=(), fact_ids=(), coverage_status='unavailable', gaps=('primary_package_unavailable',))
    # Preserve the discovery package while dating research to its already frozen observation.
    identity = 'package:research-initial:' + digest(fields)
    record_evidence_package(EvidencePackage(package_id=identity, **fields), db_path=db_path)
    return get_evidence_package(identity, db_path=db_path)


class ResearchOrchestrator:
    def __init__(self, *, planner=None, policy=None, db_path=None, clock=utc_now, monotonic=time.monotonic, sleep=time.sleep):
        self.planner = planner or ResearchPlanner()
        self.policy = policy or EvidenceResearchPolicy.from_env()
        self.db_path, self.clock, self.monotonic = db_path, clock, monotonic
        self.sleep = sleep

    def run(self, candidate):
        if self.policy.mode == 'disabled':
            return {'status': 'disabled', 'shadow': True}
        if candidate.get('research_reused'):
            return {'status': 'covered_by_existing_research', 'shadow': True,
                    'session_id': candidate['research_owner_session_id']}
        identity = candidate.get('research_session_id') or 'research:' + digest(candidate['queue_id'])
        with research_session_lock(self.db_path, identity):
            return self._run_locked(candidate, identity)

    def _run_locked(self, candidate, identity):
        self.elapsed_start = self.monotonic()
        item = get_discovery_item(candidate['queue_id'], db_path=self.db_path)
        initial = _initial_package(candidate, item, self.db_path)
        self.tools = ResearchTools(company_id=item['company_id'], event_id=item['catalyst_id'],
                                   cutoff=initial['information_cutoff_time'], db_path=self.db_path)
        try:
            self.session = get_research_session(identity, db_path=self.db_path)
        except ValueError as exc:
            if not str(exc).startswith('Unknown '):
                raise
            at = timestamp(self.clock())
            deadline = (datetime.fromisoformat(at) + timedelta(seconds=self.policy.max_seconds)).isoformat()
            session = ResearchSession(session_id=identity, queue_id=item['queue_id'], initial_package_id=initial['package_id'],
                                      started_at=at, deadline_at=deadline, model=self.planner.model or 'unconfigured',
                                      prompt_version=self.planner.prompt_version)
            record_research_session(session, db_path=self.db_path)
            self.session = get_research_session(identity, db_path=self.db_path)
        try:
            self.progress = load_research_progress(identity, db_path=self.db_path)
        except ValueError as exc:
            if not str(exc).startswith('Unknown '):
                raise
            self.progress = {'session_id': identity, 'policy': asdict(self.policy), 'initial_input': candidate,
                             'planner_identity': getattr(self.planner, 'identity', None),
                             'evidence': {'facts': [], 'documents': [], 'price_responses': [], 'comparisons': [],
                                          'conflicts': [], 'gaps': []},
                             'initialized': False, 'planner_calls': 0, 'planner_rounds': 0, 'planner_retry': None,
                             'reported_cost_estimate_usd': 0, 'retrieval_calls': 0, 'pending': None,
                             'requests': [], 'completed': [], 'unresolved': [], 'history': []}
            initialize_research_progress(self.progress, db_path=self.db_path)
        self.initial = initial
        self.policy = EvidenceResearchPolicy(**self.progress['policy'])
        self.remaining_at_start = max(0, (datetime.fromisoformat(self.session['deadline_at']) -
                                         datetime.fromisoformat(timestamp(self.clock()))).total_seconds())
        if self.session['status'] not in {'pending', 'running'}:
            return self.result()
        try:
            if self.progress['pending']:
                return self.finish('failed', 'interrupted_call_outcome_unknown_no_automatic_retry')
            if (self.session['model'] != (self.planner.model or 'unconfigured')
                    or self.session['prompt_version'] != self.planner.prompt_version
                    or self.progress['planner_identity'] != getattr(self.planner, 'identity', None)):
                return self.finish('failed', 'planner_version_changed_during_session')
            if not self.progress['initialized']:
                self.progress['evidence'] = initial_evidence(initial, self.tools)
                self.progress['initialized'] = True
                self.checkpoint({'kind': 'initial_evidence_validated'})
                if candidate['research_input']['company_evidence'].get('freshness_at_read') == 'stale':
                    return self.finish('deferred_missing_evidence', 'primary_evidence_stale')
            return self.investigate()
        except ResearchBudgetExceeded as exc:
            return self.finish('budget_exhausted', str(exc))
        except TimeoutError:
            return self.finish('budget_exhausted', 'deadline_exhausted')
        except Exception as exc:
            self.progress['raw_plan'] = None
            self.checkpoint({'kind': 'validation_or_provider_failure', 'validation': 'failed',
                             'error_type': type(exc).__name__,
                             'reason': str(exc)[:300] if isinstance(exc, ValueError) else type(exc).__name__})
            return self.finish('failed', 'research_error:' + type(exc).__name__)

    def remaining(self):
        wall = (datetime.fromisoformat(self.session['deadline_at']) - datetime.fromisoformat(timestamp(self.clock()))).total_seconds()
        monotonic = self.remaining_at_start - (self.monotonic() - self.elapsed_start)
        return min(wall, monotonic)

    def check_stop(self):
        if research_event_superseded(self.session['queue_id'], session_id=self.session['session_id'], db_path=self.db_path):
            return self.finish('superseded', 'newer_event_revision')
        if self.remaining() <= 0:
            return self.finish('budget_exhausted', 'deadline_exhausted')
        return None

    def checkpoint(self, event, allowance=None, cost_adjustment=None, refresh_request=None):
        at = timestamp(self.clock())
        if allowance:
            self.progress['pending']['reserved_day'] = at[:10]
            event = {**event, 'reserved_day': at[:10]}
        research_checkpoint(self.session, self.progress, event, at=at,
                            allowance=allowance, cost_adjustment=cost_adjustment, refresh_request=refresh_request, db_path=self.db_path)

    def reserve(self, kind, detail, cost=0):
        if kind == 'planner':
            field, limit, daily = 'planner_calls', self.policy.max_rounds * self.policy.max_planner_attempts, self.policy.daily_planner_calls
        else:
            field, limit, daily = 'retrieval_calls', self.policy.max_tool_calls, self.policy.daily_retrieval_calls
        if self.progress[field] >= limit:
            raise ResearchBudgetExceeded(kind + '_call_budget')
        nonnegative(cost)
        if self.remaining() <= 0:
            raise TimeoutError('Research deadline exhausted')
        old_calls, old_status = self.session['calls_consumed'], self.session['status']
        old_cost = self.session['cost_consumed']
        self.progress[field] += 1
        self.progress['pending'] = {'kind': kind, **detail}
        self.session.update(status='running', calls_consumed=old_calls + 1, cost_consumed=old_cost + cost)
        try:
            self.checkpoint({'kind': 'call_reserved', 'call_type': kind, **detail},
                            allowance=(kind, daily, cost, self.policy.daily_cost_budget))
        except Exception:
            self.progress[field] -= 1
            self.progress['pending'] = None
            self.session.update(status=old_status, calls_consumed=old_calls, cost_consumed=old_cost)
            raise

    def planner_context(self, required):
        evidence = self.progress['evidence']
        facts = [{**f, 'source_excerpt': f['source_excerpt'][:600]} for f in evidence['facts'][:48]]
        documents = [{**d, 'text': d['text'][:2000]} for d in evidence['documents'][:24]]
        return {'company_id': self.tools.company_id, 'event_id': self.tools.event_id,
                'as_of': self.tools.cutoff, 'operations': OPERATIONS,
                'company': self.progress['initial_input']['research_input']['company'],
                'articles': [{**a, 'body': (a.get('body') or '')[:3000]} for a in
                             self.progress['initial_input']['research_input'].get('evidence', [])[:10]],
                'evidence': {**evidence, 'facts': facts, 'documents': documents},
                'context_truncated': len(facts) < len(evidence['facts']) or len(documents) < len(evidence['documents']),
                'unresolved_questions': self.progress['unresolved'], 'previous_requests': self.progress['history'],
                'required_requests': [asdict(r) for r in required],
                'remaining_retrieval_calls': self.policy.max_tool_calls - self.progress['retrieval_calls']}

    def plan(self):
        retry = self.progress.get('planner_retry')
        if retry:
            delay = max(0, (datetime.fromisoformat(retry['not_before']) -
                            datetime.fromisoformat(timestamp(self.clock()))).total_seconds())
            if delay >= self.remaining():
                raise ResearchBudgetExceeded('planner_retry_deadline')
            self.sleep(delay)
            stopped = self.check_stop()
            if stopped:
                return stopped
            context, attempt = retry['input'], retry['attempt'] + 1
        else:
            if self.progress.get('planner_rounds', self.progress['planner_calls']) >= self.policy.max_rounds:
                raise ResearchBudgetExceeded('planner_call_budget')
            required = required_requests(self.progress['evidence'], self.tools, self.progress['completed'])
            context, attempt = self.planner_context(required), 1
            if context['context_truncated']:
                self.progress['evidence']['gaps'] = sorted(set(self.progress['evidence']['gaps'] + ['planner_context_truncated']))
        if hasattr(self.planner, 'reservation'):
            reservation = self.planner.reservation(context)
        else:
            if self.planner.cost_per_call != 0:
                raise ValueError('Paid research adapter needs a reservation contract')
            reservation = {'cost_reserved_usd': 0}
        if not retry:
            self.progress['planner_rounds'] = self.progress.get('planner_rounds', 0) + 1
        self.reserve('planner', {'input': context, 'model': self.session['model'], 'attempt': attempt,
                                'prompt_version': self.session['prompt_version'], 'reservation': reservation},
                     cost=reservation['cost_reserved_usd'])
        day = self.progress['pending']['reserved_day']
        try:
            raw = self.planner.plan(context, timeout=self.remaining())
        except ResearchProviderError as exc:
            self.record_planner_usage(reservation, day)
            self.progress['pending'] = None
            self.progress['planner_retry'] = None
            if exc.retryable and attempt < self.policy.max_planner_attempts:
                delay = min(30, self.policy.retry_seconds * 2 ** (attempt - 1))
                next_at = datetime.fromisoformat(timestamp(self.clock())) + timedelta(seconds=delay)
                self.progress['planner_retry'] = {'input': context, 'attempt': attempt, 'not_before': next_at.isoformat()}
            self.checkpoint({'kind': 'planner_attempt_failed', 'attempt': attempt,
                             'diagnostics': exc.diagnostics, 'retry_scheduled': bool(self.progress['planner_retry'])})
            if self.progress['planner_retry']:
                return None
            return self.finish('failed', 'planner_provider_failure')
        except Exception:
            self.record_planner_usage(reservation, day)
            raise
        self.record_planner_usage(reservation, day)
        canonical(raw)
        self.progress['pending'] = None
        self.progress['planner_retry'] = None
        self.progress['raw_plan'] = raw
        self.checkpoint({'kind': 'planner_response', 'response': raw})
        return self.apply_plan()

    def record_planner_usage(self, reservation, day):
        response = getattr(self.planner, 'last_response', {})
        if not response:
            return
        usage = response.get('usage', {})
        estimated_cost = self.planner.policy.usage_cost(usage) if billed(reservation) else 0
        extra = max(0, (estimated_cost or 0) - reservation['cost_reserved_usd'])
        self.session['cost_consumed'] += extra
        self.progress['reported_cost_estimate_usd'] += estimated_cost or 0
        self.checkpoint({'kind': 'planner_usage', **response, 'reservation': reservation,
                         'reported_cost_estimate_usd': estimated_cost, 'cost_status': 'estimated_from_usage' if estimated_cost is not None else 'unknown'},
                        cost_adjustment=(day, extra) if extra else None)
        if (extra or (type(usage.get('prompt_token_count')) is int
                      and usage['prompt_token_count'] > reservation.get('input_tokens_reserved', float('inf')))
                or (type(usage.get('candidates_token_count')) is int and type(usage.get('thoughts_token_count')) is int
                    and usage['candidates_token_count'] + usage['thoughts_token_count'] > reservation.get('output_tokens_reserved', float('inf')))):
            raise ResearchBudgetExceeded('provider_usage_exceeded_reservation')

    def apply_plan(self):
        plan = validate_plan(self.progress['raw_plan'])
        required = required_requests(self.progress['evidence'], self.tools, self.progress['completed'])
        requested = [self.tools.parse(r) for r in plan['requests']]
        if len({r.key for r in requested}) != len(requested) or any(r.key in self.progress['completed'] for r in requested):
            return self.finish('deferred_missing_evidence', 'repeated_request')
        if plan['outcome'] in {'deferred_missing_evidence', 'conflicting_evidence'} and not required:
            status, reason = coverage_outcome(self.progress)
            return self.finish('conflicting_evidence' if status == 'conflicting_evidence' else 'deferred_missing_evidence', plan['reason'])
        requests = {r.key: r for r in required}
        for request in requested:
            # Either the planner or deterministic coverage checks can make a question critical.
            existing = requests.get(request.key)
            requests[request.key] = replace(request, critical=request.critical or bool(existing and existing.critical))
        self.progress['requests'] = [asdict(r) for r in requests.values()]
        self.progress['batch_before'] = evidence_identity(self.progress['evidence'])
        self.progress['requested_outcome'] = plan['outcome']
        self.progress['raw_plan'] = None
        self.checkpoint({'kind': 'plan_validated', 'requests': self.progress['requests']})
        if not requests:
            if plan['outcome'] == 'ready':
                return self.finish(*coverage_outcome(self.progress))
            return self.finish('deferred_missing_evidence', 'no_progress')
        return None

    def retrieve(self):
        request = self.tools.parse(self.progress['requests'][0])
        self.reserve('retrieval', {'request': asdict(request), 'request_key': request.key, 'provider': self.tools.provider})
        result = self.tools.execute(request, timeout=self.remaining())
        self.progress['pending'] = None
        self.progress['requests'].pop(0)
        self.progress['completed'].append(request.key)
        self.progress['history'].append({'request': asdict(request), 'available': result['available'], 'gaps': result['gaps']})
        self.progress['evidence'] = merge_evidence(self.progress['evidence'], result)
        if request.critical and not result['available']:
            self.progress['unresolved'].append(asdict(request))
        self.checkpoint({'kind': 'retrieval_result', 'request': asdict(request), 'request_key': request.key,
                         'validation': 'passed', 'result': result})

    def investigate(self):
        while True:
            stopped = self.check_stop()
            if stopped:
                return stopped
            if self.progress.get('raw_plan') is not None:
                stopped = self.apply_plan()
                if stopped:
                    return stopped
            if self.progress['requests']:
                self.retrieve()
                continue
            if self.progress.get('batch_before') is not None:
                status, reason = coverage_outcome(self.progress)
                if status in {'conflicting_evidence', 'deferred_missing_evidence'} and self.progress['unresolved']:
                    return self.finish(status, reason)
                if status == 'conflicting_evidence' or self.progress['requested_outcome'] == 'ready':
                    return self.finish(status, reason)
                if evidence_identity(self.progress['evidence']) == self.progress['batch_before']:
                    return self.finish('deferred_missing_evidence', 'no_progress')
                self.progress['batch_before'] = None
            stopped = self.plan()
            if stopped:
                return stopped

    def finish(self, status, reason):
        # Supersession takes precedence even when it occurs during a provider call.
        if status != 'superseded' and research_event_superseded(self.session['queue_id'], session_id=self.session['session_id'], db_path=self.db_path):
            status, reason = 'superseded', 'newer_event_revision'
        if status == 'ready' and self.remaining() <= 0:
            status, reason = 'budget_exhausted', 'deadline_exhausted'
        self.session.update(status=status, stop_reason=reason)
        self.session['final_package_id'] = (final_package(self.initial, self.progress, db_path=self.db_path)
                                            if self.progress['initialized'] else self.initial['package_id'])
        self.progress['pending'] = None
        refresh = None
        if status == 'deferred_missing_evidence' and not self.progress['initial_input'].get('continuation'):
            refresh = {'company_id': self.tools.company_id}
            self.progress['evidence_refresh_requested'] = True
        self.checkpoint({'kind': 'session_finished', 'status': status, 'reason': reason,
                         'evidence_refresh_requested': bool(refresh)}, refresh_request=refresh)
        return self.result()

    def result(self):
        return {**self.session, 'shadow': True, 'continuation': self.progress['initial_input'].get('continuation'),
                'evidence_refresh_requested': self.progress.get('evidence_refresh_requested', False), 'planner_calls': self.progress['planner_calls'],
                'retrieval_calls': self.progress['retrieval_calls'],
                'reported_cost_estimate_usd': self.progress.get('reported_cost_estimate_usd', 0), 'unresolved_questions': self.progress['unresolved']}


def process_research_queue(*, policy=None, planner=None, clock=utc_now, limit=6, db_path=None):
    policy = policy or EvidenceResearchPolicy.from_env()
    if policy.mode == 'disabled':
        return {'status': 'disabled', 'sessions': []}
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Research batch limit must be between 1 and 100')
    initialize_database(db_path=db_path)
    failures = prepare_research_continuations(limit=limit, db_path=db_path)
    results = []
    for candidate in pending_research_candidates(limit=limit, db_path=db_path):
        try:
            orchestrator = ResearchOrchestrator(policy=policy, planner=planner, clock=clock, db_path=db_path)
            results.append(orchestrator.run(candidate))
        except ResearchSessionBusy:
            continue
        except Exception as exc:
            failures.append({'queue_id': candidate['queue_id'], 'reason': type(exc).__name__})
    return {'status': 'shadow', 'sessions': results, 'failures': failures}
