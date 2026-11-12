"""Provenance and catalyst decay policies; observational by default."""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import logging
import os

from catalyst_models import timestamp, DECAY_BASIS
from db_common import get_connection
from market_db_modules.company_evidence import get_evidence_document, is_issuer_document, latest_evidence_package
from market_db_modules.shared import find_company_row_by_symbol
from processing.scoring import recency_score
from market_db_modules.catalyst_research import record_catalyst_gate_observation
from services.research.research_promotion import guard_policy

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CatalystGatePolicy:
    provenance_mode: str = 'shadow'
    freshness_mode: str = 'shadow'
    minimum_roots: int = 2
    primary_evidence_threshold: float = .75
    minimum_remaining_value: float = .25
    version: str = 'catalyst-gates-v3'

    @classmethod
    def from_env(cls):
        def mode(name):
            value = os.getenv(name, 'shadow').lower().strip()
            if value not in {'shadow', 'enforce', 'disabled'}:
                raise ValueError(f'{name} must be shadow, enforce, or disabled')
            return value
        return guard_policy(cls(provenance_mode=mode('V2_PROVENANCE_MODE'), freshness_mode=mode('V2_FRESHNESS_MODE')),
                            {'provenance_mode': 'PROVENANCE', 'freshness_mode': 'FRESHNESS'})


def _result(stage, reason, passed, mode, metrics, policy):
    """`passed` is None when an input is missing: no verdict, yet still no entry under enforcement."""
    return {'stage': stage, 'reason_codes': [reason], 'would_allow': passed,
            'allowed': bool(passed) or mode != 'enforce', 'enforcement_mode': mode,
            'metrics': metrics, 'thresholds': asdict(policy), 'policy_version': policy.version}


def with_primary_filing(state, symbol, *, as_of, db_path=None):
    """Count the company's own filing from the event window as a primary root.

    The news feed is mostly republished commentary, so the issuer's filing is often the only
    independent source. Only filings published from a day before the event up to `as_of` count.
    """
    first, cutoff = timestamp(state.get('first_public_time')), timestamp(as_of)
    if not (first and cutoff):
        return state
    try:
        with closing(get_connection(db_path)) as conn:
            company = find_company_row_by_symbol(symbol, conn=conn)
        package = company and latest_evidence_package(company['id'], as_of=cutoff.isoformat(), db_path=db_path)
        documents = [get_evidence_document(identity, db_path=db_path) for identity in (package or {}).get('document_ids', [])]
    except Exception:
        LOGGER.exception('Primary filing lookup failed for %s', symbol)
        return state
    filings = [doc for doc in documents if is_issuer_document(doc)
               and first - timedelta(days=1) <= timestamp(doc['published_at']) <= cutoff]
    if not filings:
        return state
    latest = max(filings, key=lambda doc: doc['published_at'])
    return {**state, 'primary_source_present': True, 'primary_filing_document_id': latest['document_id'],
            'independent_root_count': (state.get('independent_root_count') or 0) + 1}


def _freshness_reason(age, remaining, article_count, policy):
    if age is None:
        return 'no_catalyst_articles' if article_count == 0 else 'missing_or_future_event_time'
    if remaining is None:
        return 'unavailable_half_life_prior'
    return 'fresh' if remaining >= policy.minimum_remaining_value else 'decayed_catalyst'


def evaluate_catalyst_gates(state, *, as_of=None, policy=None):
    policy = policy or CatalystGatePolicy.from_env()
    now = as_of or datetime.now(timezone.utc)
    count = state.get('independent_root_count', 0)
    score = state.get('evidence_score')
    primary_exception = count == 1 and state.get('primary_source_present') and score is not None and score >= policy.primary_evidence_threshold
    passed = count >= policy.minimum_roots or bool(primary_exception)
    provenance = _result('PROVENANCE', 'pass_independent_roots' if count >= policy.minimum_roots else
                         'pass_with_single_primary_root' if primary_exception else 'insufficient_independent_roots',
                         passed, policy.provenance_mode,
                         {'catalyst_id': state['catalyst_id'], 'independent_root_count': count,
                          'primary_source_present': state.get('primary_source_present', False),
                          'evidence_score': score, 'single_primary_exception': bool(primary_exception),
                          'primary_filing_document_id': state.get('primary_filing_document_id')}, policy)
    first = timestamp(state.get('first_public_time'))
    latest_root = timestamp(state.get('latest_root_event_time'))
    age = (now-first).total_seconds()/60 if first and first <= now else None
    refreshed = bool(first and latest_root and first < latest_root <= now)
    effective_age = (now-latest_root).total_seconds()/60 if refreshed else age
    half_life = state.get('expected_half_life_minutes')
    remaining = recency_score(effective_age/1440, half_life_days=half_life/1440) if effective_age is not None and half_life and half_life > 0 else None
    reason = _freshness_reason(age, remaining, state.get('article_count'), policy)
    freshness = _result('FRESHNESS', reason, None if remaining is None else remaining >= policy.minimum_remaining_value,
                        policy.freshness_mode, {'catalyst_id': state['catalyst_id'], 'catalyst_family': state['catalyst_family'],
                        'catalyst_age_minutes': age, 'effective_age_minutes': effective_age,
                        'remaining_information_value': remaining, 'half_life_minutes': half_life,
                        'decay_basis': DECAY_BASIS, 'calibration': 'research_prior',
                        'refreshed_by_new_root_event': refreshed, 'first_public_time': state.get('first_public_time'),
                        'first_public_time_basis': state.get('first_public_time_basis'),
                        'latest_root_event_time': state.get('latest_root_event_time')}, policy)
    return {'PROVENANCE': provenance, 'FRESHNESS': freshness}


def record_catalyst_gates(context):
    from services.runtime.candidate_lifecycle import checkpoint, reject, observe
    state = with_primary_filing(context['catalysts'][0], context['symbol'], as_of=context['created_at'],
                                db_path=context.get('db_path'))
    gates = evaluate_catalyst_gates(state, as_of=timestamp(context['created_at']))
    for stage, gate in gates.items():
        checkpoint(context, stage, gate)
        observe(record_catalyst_gate_observation, context, gate)
        if gate['would_allow'] is False and gate['enforcement_mode'] != 'disabled':
            reject(context, stage, gate['reason_codes'][0], metrics=gate['metrics'], thresholds=gate['thresholds'],
                   shadow=gate['enforcement_mode'] == 'shadow')
    return gates


def catalyst_admission_allowed(context, *, policy=None):
    policy = policy or CatalystGatePolicy.from_env()
    gates = (context or {}).get('catalyst_gates') or {}
    for stage, mode in (('PROVENANCE', policy.provenance_mode), ('FRESHNESS', policy.freshness_mode)):
        if mode == 'enforce' and (stage not in gates or not gates[stage].get('would_allow')):
            return False
    return not any(gate.get('allowed') is False for gate in gates.values())
