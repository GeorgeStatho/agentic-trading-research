"""Collector-owned ingestion; decision payloads only read its persisted packages."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlparse

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database
from market_db_modules.company_collection import (
    collection_state, get_issuer_mapping, save_collection_state, update_issuer_mapping,
)
from services.company_evidence_extraction import ingest_source
from services.company_evidence_packages import build_company_package
from services.company_evidence_policy import CompanyEvidencePolicy, issuer_configuration
from Webscraping.company_evidence.issuer import IssuerReleaseAdapter
from Webscraping.company_evidence.sec import SECAdapter
from Webscraping.company_evidence.transport import EvidenceHTTP, utc_now
from market_db_modules.evidence_refresh import due_refresh_companies, due_refresh_requests, finish_refresh_requests
from market_db_modules.evidence_refresh import finish_refresh_requests
from services.company_evidence_demand import enqueue_company_interest
from services.research_lock import ResearchSessionBusy, research_session_lock


def collect_company(company, adapters, policy, *, db_path=None, clock=utc_now):
    documents, gaps, outcomes = [], [], {}
    for adapter in adapters:
        state = collection_state(adapter.name, str(company['id']), db_path=db_path)
        if state.get('next_attempt_at', '') > clock().isoformat():
            documents.extend(state.get('document_ids', []))
            gaps.extend(state.get('gaps', []))
            outcomes[adapter.name] = 'cached'
            continue
        try:
            result = adapter.collect(company, state.get('cursor', {}))
            ids = []
            provider_gaps = list(result.gaps)
            for source in result.documents:
                if source.metadata.get('cik') != company['cik']:
                    raise ValueError('Evidence source issuer mismatch')
                identity, _ = ingest_source(company, source, clock().isoformat(), db_path=db_path)
                ids.append(identity)
                provider_gaps.extend(source.gaps)
            saved = {'cursor': result.cursor, 'document_ids': ids, 'gaps': provider_gaps, 'failures': 0,
                     'next_attempt_at': (clock() + timedelta(seconds=policy.refresh_seconds)).isoformat()}
            save_collection_state(adapter.name, str(company['id']), saved, clock().isoformat(), db_path=db_path)
            documents.extend(ids)
            gaps.extend(provider_gaps)
            outcomes[adapter.name] = 'collected'
        except Exception as exc:
            failures = state.get('failures', 0) + 1
            reason = f'{adapter.name}_collection_failed:{type(exc).__name__}'
            gaps.append(reason)
            saved = {**state, 'failures': failures, 'gaps': list(set(state.get('gaps', []) + [reason])),
                     'next_attempt_at': (clock() + timedelta(seconds=min(policy.refresh_seconds, 60 * 2 ** min(failures, 8)))).isoformat()}
            save_collection_state(adapter.name, str(company['id']), saved, clock().isoformat(), db_path=db_path)
            documents.extend(state.get('document_ids', []))
            outcomes[adapter.name] = reason
    package_id = build_company_package(company, documents, clock().isoformat(), policy, db_path=db_path, gaps=gaps)
    return {'company_id': company['id'], 'symbol': company['symbol'], 'package_id': package_id, 'providers': outcomes}


def collect_company_evidence(*, db_path=None, policy=None, clock=utc_now, consensus_adapter=None):
    policy = policy or CompanyEvidencePolicy.from_env()
    if not policy.enabled:
        return {'status': 'disabled', 'companies': []}
    try:
        with research_session_lock(db_path, 'company-evidence-collector'):
            return _collect_requested_evidence(db_path, policy, clock, consensus_adapter)
    except ResearchSessionBusy:
        return {'status': 'busy', 'companies': []}


def _collect_requested_evidence(db_path, policy, clock, consensus_adapter):
    user_agent = os.getenv('SEC_USER_AGENT', '').strip()
    if '@' not in user_agent:
        return {'status': 'configuration_required', 'reason': 'SEC_USER_AGENT must identify the operator and contact email'}
    initialize_database(db_path=db_path)
    config = issuer_configuration()
    at = clock().isoformat()
    enqueue_company_interest(at=at, policy=policy, db_path=db_path)
    companies = due_refresh_companies(at=at, limit=policy.companies_per_cycle, db_path=db_path)
    if not companies:
        return {'status': 'idle', 'companies': []}
    sec = SECAdapter(EvidenceHTTP('sec', ('www.sec.gov', 'data.sec.gov'), policy, db_path, user_agent, clock=clock), policy)
    try:
        mapping = sec.issuer_map()
    except Exception as exc:
        return {'status': 'provider_unavailable', 'reason': f'sec_issuer_map:{type(exc).__name__}', 'companies': []}
    outcomes = []
    for company in companies:
        requests = due_refresh_requests(company['id'], at=clock().isoformat(), db_path=db_path)
        try:
            result = _collect_resolved_company(company, config, mapping, sec, policy, user_agent,
                                               clock, consensus_adapter, db_path)
            _complete_company_requests(requests, result, company, clock, db_path)
            outcomes.append(result)
        except Exception as exc:
            reason = 'collection_failed:' + type(exc).__name__
            outcomes.append({'symbol': company['symbol'], 'status': reason})
            finish_refresh_requests(requests, at=clock().isoformat(), reason=reason, db_path=db_path)
    return {'status': 'completed', 'companies': outcomes, 'consensus': 'configured' if consensus_adapter else 'unavailable'}


def _collect_resolved_company(company, config, mapping, sec, policy, user_agent, clock, consensus_adapter, db_path):
    settings = config.get(company['symbol'].upper(), {})
    cik = settings.get('cik') or mapping.get(company['symbol'].upper().replace('.', '-'))
    if not cik:
        return {'symbol': company['symbol'], 'status': 'issuer_mapping_unavailable'}
    update_issuer_mapping(company['id'], company['symbol'], cik,
                          'configured_override' if settings.get('cik') else 'sec_tickers',
                          clock().isoformat(), db_path=db_path)
    company['cik'] = get_issuer_mapping(company['id'], db_path=db_path)['cik']
    adapters = [sec]
    if settings.get('feed_url'):
        if settings.get('allow_internal_cache') is not True or not settings.get('allowed_hosts'):
            return {'symbol': company['symbol'], 'status': 'issuer_feed_configuration_invalid'}
        provider = 'issuer:' + str(urlparse(settings['feed_url']).hostname)
        http = EvidenceHTTP(provider, settings['allowed_hosts'], policy, db_path, user_agent, clock=clock)
        adapters.append(IssuerReleaseAdapter(http, settings['feed_url'], policy))
    if consensus_adapter:
        adapters.append(consensus_adapter)
    return collect_company(company, adapters, policy, db_path=db_path, clock=clock)


def _complete_company_requests(requests, result, company, clock, db_path):
    at = clock().isoformat()
    states = [collection_state(provider, str(company['id']), db_path=db_path) for provider in result.get('providers', {})]
    failures = [state for state in states if state.get('failures', 0)]
    reason = result.get('status') or ('provider_collection_failed' if failures else None)
    if not reason and not result.get('package_id'):
        reason = 'primary_evidence_unavailable'
    retry_at = max((s['next_attempt_at'] for s in failures), default=None)
    # A recent successful fetch can satisfy ordinary demand from cache. A missing-evidence
    # investigation waits for the scheduled provider refresh instead of bypassing its TTL.
    waiting = [r for r in requests if r['parent_session_id'] and not reason
               and any(value == 'cached' for value in result.get('providers', {}).values())]
    if waiting:
        next_at = max((s.get('next_attempt_at', at) for s in states), default=at)
        with closing(get_connection(db_path)) as conn, conn:
            conn.executemany("UPDATE company_evidence_requests SET next_attempt_at=? WHERE request_id=? AND status='pending'",
                             [(next_at, r['request_id']) for r in waiting])
    finish_refresh_requests([r for r in requests if r not in waiting], at=at, package_id=result.get('package_id'),
                            reason=reason, retry_at=retry_at, db_path=db_path)
