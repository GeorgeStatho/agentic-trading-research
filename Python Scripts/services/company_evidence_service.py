"""Collector-owned ingestion; decision payloads only read its persisted packages."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlparse

from db_common import DB_PATH, get_connection
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


def collect_company(company, adapters, policy, *, db_path=DB_PATH, clock=utc_now):
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


def collect_company_evidence(*, db_path=DB_PATH, policy=None, clock=utc_now, consensus_adapter=None):
    policy = policy or CompanyEvidencePolicy.from_env()
    if not policy.enabled:
        return {'status': 'disabled', 'companies': []}
    user_agent = os.getenv('SEC_USER_AGENT', '').strip()
    if '@' not in user_agent:
        return {'status': 'configuration_required', 'reason': 'SEC_USER_AGENT must identify the operator and contact email'}
    initialize_database(db_path=db_path)
    config = issuer_configuration()
    from services.discovery_policy import DiscoveryPolicy
    from market_db_modules.discovery_sources import select_discovery_companies, mark_discovery_scanned
    discovery_policy = DiscoveryPolicy.from_env()
    symbols = {s.strip().upper() for s in os.getenv('COMPANY_EVIDENCE_SYMBOLS', '').split(',') if s.strip()} | set(config)
    if discovery_policy.mode == 'shadow':
        symbols = set(discovery_policy.symbols)
    if not symbols:
        return {'status': 'configuration_required', 'reason': 'Set COMPANY_EVIDENCE_SYMBOLS or an issuer configuration file'}
    with closing(get_connection(db_path)) as conn:
        companies = [dict(row) for row in conn.execute('SELECT id,symbol,name FROM companies ORDER BY id')
                     if row['symbol'].upper() in symbols]
    if discovery_policy.mode == 'shadow':
        companies = select_discovery_companies(discovery_policy, at=clock().isoformat(), lane='collection',
                                               limit=policy.companies_per_cycle, db_path=db_path)
    sec = SECAdapter(EvidenceHTTP('sec', ('www.sec.gov', 'data.sec.gov'), policy, db_path, user_agent, clock=clock), policy)
    try:
        mapping = sec.issuer_map()
    except Exception as exc:
        return {'status': 'provider_unavailable', 'reason': f'sec_issuer_map:{type(exc).__name__}', 'companies': []}
    position = (0 if discovery_policy.mode == 'shadow' else
                collection_state('collector', 'company_cursor', db_path=db_path).get('position', 0))
    outcomes = []
    for offset in range(min(policy.companies_per_cycle, len(companies))):
        company = companies[(position + offset) % len(companies)]
        if discovery_policy.mode == 'shadow':
            mark_discovery_scanned(company['id'], at=clock().isoformat(), lane='collection', db_path=db_path)
        settings = config.get(company['symbol'].upper(), {})
        cik = settings.get('cik') or mapping.get(company['symbol'].upper().replace('.', '-'))
        if not cik:
            outcomes.append({'symbol': company['symbol'], 'status': 'issuer_mapping_unavailable'})
            continue
        update_issuer_mapping(company['id'], company['symbol'], cik,
                              'configured_override' if settings.get('cik') else 'sec_tickers',
                              clock().isoformat(), db_path=db_path)
        company['cik'] = get_issuer_mapping(company['id'], db_path=db_path)['cik']
        adapters = [sec]
        if settings.get('feed_url'):
            if settings.get('allow_internal_cache') is not True or not settings.get('allowed_hosts'):
                outcomes.append({'symbol': company['symbol'], 'status': 'issuer_feed_configuration_invalid'})
                continue
            provider = 'issuer:' + str(urlparse(settings['feed_url']).hostname)
            http = EvidenceHTTP(provider, settings['allowed_hosts'], policy, db_path, user_agent, clock=clock)
            adapters.append(IssuerReleaseAdapter(http, settings['feed_url'], policy))
        if consensus_adapter:
            adapters.append(consensus_adapter)
        outcomes.append(collect_company(company, adapters, policy, db_path=db_path, clock=clock))
    if companies:
        save_collection_state('collector', 'company_cursor', {'position': (position + len(outcomes)) % len(companies)},
                              clock().isoformat(), db_path=db_path)
    return {'status': 'completed', 'companies': outcomes, 'consensus': 'configured' if consensus_adapter else 'unavailable'}
