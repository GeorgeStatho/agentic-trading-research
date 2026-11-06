"""Merge primary disclosures and company-linked provenance into a shadow review queue."""
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import urlsplit

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database
from market_db_modules.company_evidence import (
    get_company_fact, get_evidence_document, latest_evidence_package, record_evidence_package,
)
from market_db_modules.discovery_queue import enqueue_discovery_event
from market_db_modules.discovery_sources import (
    company_event_articles, get_catalyst, latest_discovery_event, mark_discovery_scanned,
    sector_support_articles, watchlist_snapshot,
)
from market_db_modules.foundation_store import digest
from research_foundation_contracts import DiscoveryQueueItem, EvidencePackage, timestamp
from services.discovery.discovery_policy import DiscoveryPolicy
from catalyst_models import PRIMARY_SOURCE_TYPES
from market_db_modules.company_interest import due_discovery_checks, finish_discovery_check
from market_db_modules.evidence_refresh import enqueue_refresh
from news_db_modules.catalysts import build_catalyst_state, store_catalyst_state
from services.discovery.company_evidence_demand import request_pipeline_evidence
from services.discovery.company_evidence_policy import CompanyEvidencePolicy
from services.discovery.company_interest import reconcile_company_interest
from services.core.trading_config import AgentPipelineSettings
from services.research.research_lock import research_session_lock, ResearchSessionBusy


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _normalized_text(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip().lower()


def _event_revision(company_id, catalyst_id, signature, db_path):
    prior = latest_discovery_event(company_id, catalyst_id, db_path=db_path)
    if not prior:
        return digest(signature), None
    if json.loads(prior['payload_json']).get('signature') == signature:
        return prior['evidence_revision'], prior
    return digest({'content': signature, 'supersedes': prior['evidence_revision']}), prior


def _predecessor_revision(prior, revision):
    if prior is None:
        return None
    if prior['evidence_revision'] != revision:
        return prior['evidence_revision']
    return json.loads(prior['payload_json']).get('predecessor_revision')


def _url_key(url):
    parsed = urlsplit(str(url or ''))
    return (parsed.hostname or '').lower().removeprefix('www.') + parsed.path.rstrip('/')


def _root_articles(articles):
    roots = {}
    for article in sorted(articles, key=lambda a: (a.get('syndication_parent_id') is not None, a['id'])):
        # Unverified rewrites do not manufacture independent roots.
        key = article.get('root_event_id') or 'unverified'
        roots.setdefault(key, article)
    return list(roots.values())


def _package_signature(package, db_path):
    facts = [get_company_fact(identity, db_path=db_path) for identity in package['fact_ids']]
    superseded = {fact.get('supersedes_fact_id') for fact in facts}
    keys = ('metric', 'kind', 'value', 'range_high', 'fiscal_period', 'period_type', 'accounting_basis', 'unit', 'currency', 'scale')
    values = {digest({key: fact.get(key) for key in keys}) for fact in facts
              if fact['fact_id'] not in superseded and fact['kind'] != 'derived'}
    documents = [get_evidence_document(identity, db_path=db_path) for identity in package['document_ids']]
    obsolete = {doc.get('supersedes_document_id') for doc in documents}
    # Normalized text captures new qualitative disclosures as well as extracted numbers.
    texts = {digest(_normalized_text(doc['text'])) for doc in documents
             if doc['document_id'] not in obsolete and doc['document_type'] != 'xbrl_facts'}
    schedules = [{k: v for k, v in event.items() if k != 'document_id'} for event in package.get('scheduled_events', [])
                 if not event.get('superseded')]
    return {'facts': sorted(values), 'documents': sorted(texts), 'schedules': sorted(schedules, key=digest)}, documents


def _match_package_catalyst(package, documents, articles):
    if documents:
        latest = max(documents, key=lambda doc: (doc['published_at'], doc['ingested_at']))
        event_key = latest.get('metadata', {}).get('event_key')
        documents = [doc for doc in documents if doc.get('metadata', {}).get('event_key') == event_key]
    urls = {_url_key(doc['source_url']) for doc in documents}
    accessions = {doc.get('metadata', {}).get('accession', '').replace('-', '') for doc in documents} - {''}
    for article in articles:
        links = [article.get('primary_source_url'), article.get('source_url')]
        if any(_url_key(url) in urls or any(a in str(url).replace('-', '') for a in accessions) for url in links if url):
            return article['catalyst_id']
    return package['catalyst_id']


def _primary_root(company_id, catalyst_id, documents, known_roots, db_path):
    if known_roots or not documents:
        return known_roots
    filings = [doc for doc in documents if doc['document_type'] != 'xbrl_facts']
    latest = max(filings or documents, key=lambda doc: (doc['published_at'], doc['ingested_at']))
    identity = 'root:company-evidence:' + digest([company_id, latest.get('metadata', {}).get('event_key', latest['source_url'])])
    with closing(get_connection(db_path)) as conn, conn:
        # Keep the same root when a primary source is subsequently linked to a news catalyst.
        source = ('sec-accession:' + str(company_id) + ':' + latest['metadata']['accession']
                  if latest.get('metadata', {}).get('accession') else 'url:' + latest['source_url'])
        row = conn.execute('SELECT root_event_id FROM root_events WHERE root_source_id=?', (source,)).fetchone()
        if row:
            return [row[0]]
        conn.execute('''INSERT OR IGNORE INTO root_events VALUES (?,?,?,?,?,?,?)''',
                     (identity, catalyst_id, source, latest['published_at'],
                      latest['first_observed_at'], 1, 1))
    return [identity]


def company_discovery_events(company, *, at, policy, db_path=None):
    articles = company_event_articles(company['id'], at=at, stale_seconds=policy.stale_seconds, db_path=db_path)
    groups = defaultdict(list)
    for article in articles:
        groups[article['catalyst_id']].append(article)
    package = latest_evidence_package(company['id'], as_of=at, db_path=db_path)
    package_signature, documents, package_catalyst = None, [], None
    if package:
        package_signature, documents = _package_signature(package, db_path)
        package_catalyst = _match_package_catalyst(package, documents, articles)
        groups.setdefault(package_catalyst, [])
    events = []
    for catalyst_id, members in groups.items():
        catalyst = get_catalyst(catalyst_id, db_path=db_path)
        if catalyst is None:
            continue
        roots = _root_articles(members)
        signature = {'roots': [{'root': row.get('root_event_id'),
                               'content': digest(_normalized_text(row.get('title')) + ' ' +
                                                 _normalized_text(row.get('body') or row.get('summary')))} for row in roots]}
        event_package = package if catalyst_id == package_catalyst else None
        if event_package:
            signature['primary_evidence'] = package_signature
        revision, prior = _event_revision(company['id'], catalyst_id, signature, db_path)
        change = 'initial' if prior is None else 'material'
        if prior and (any(re.search(r'\bcorrect(?:ed|ion)|\brestat', row.get('title', ''), re.I) for row in roots)
                      or any('/A' in doc.get('metadata', {}).get('form', '') for doc in documents)):
            change = 'correction'
        if event_package:
            event_package = _event_package(event_package, catalyst_id, revision, db_path)
        source_times = [row['event_time'] for row in roots if row.get('event_time')]
        if event_package:
            source_times.extend(doc['published_at'] for doc in documents)
        root_ids = sorted({row['root_event_id'] for row in roots if row.get('root_event_id')})
        if event_package:
            root_ids = _primary_root(company['id'], catalyst_id, documents, root_ids, db_path)
            catalyst = {**catalyst, 'root_event_ids': root_ids, 'primary_source_present': True}
        public_at = max(source_times, key=lambda value: datetime.fromisoformat(timestamp(value))) if source_times else at
        events.append({'company': company, 'catalyst_id': catalyst_id, 'evidence_revision': revision,
                       'package_id': event_package['package_id'] if event_package else None,
                       'articles': roots, 'root_event_ids': root_ids,
                       'catalyst': catalyst, 'published_at': timestamp(public_at), 'observed_at': at,
                       'change_kind': change, 'signature': signature,
                       'predecessor_revision': _predecessor_revision(prior, revision)})
    return events


def _event_package(package, catalyst_id, revision, db_path):
    fields = {**package, 'catalyst_id': catalyst_id, 'event_revision': revision}
    fields.pop('package_id')
    fields['package_id'] = 'package:discovery:' + digest(fields)
    record_evidence_package(EvidencePackage(**fields), db_path=db_path)
    return fields


def priority_components(event, purpose, *, at):
    catalyst = event['catalyst']
    primary = (bool(event['package_id']) or catalyst.get('primary_source_present', False)
               or any(article.get('source_type') in PRIMARY_SOURCE_TYPES for article in event.get('articles', [])))
    age_hours = max(0, (datetime.fromisoformat(at) - datetime.fromisoformat(event['published_at'])).total_seconds() / 3600)
    return {'materiality': 20 if event['package_id'] or catalyst.get('catalyst_type') in {'earnings', 'guidance'} else 10,
            'novelty': 10, 'source_reliability': 20 if primary else 10,
            'freshness': max(0, 20 - age_hours / 12),
            'review_urgency': 20 if purpose == 'position_review' else 10 if purpose == 'watchlist' else 0}


def _sector_event(company, *, at, policy, db_path):
    articles = sector_support_articles(company['id'], at=at, stale_seconds=policy.stale_seconds, db_path=db_path)
    catalyst_id = 'catalyst:sector-discovery:' + str(company['id'])
    catalyst = build_catalyst_state(catalyst_id, [], ticker=company['symbol'])
    with closing(get_connection(db_path)) as conn, conn:
        store_catalyst_state(catalyst, conn=conn)
    for article in articles:
        article['event_time'] = article.get('event_time') or timestamp(article['published_at'])
    watchlist = watchlist_snapshot(company['id'], at=at, db_path=db_path)
    watchlist_content = {k: v for k, v in (watchlist or {}).items() if k != 'updated_at'}
    source_time = (watchlist or {}).get('updated_at')
    if source_time:
        parsed = datetime.fromisoformat(source_time.replace('Z', '+00:00'))
        source_time = parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).isoformat()
    signature = {'articles': [{'id': a['id'], 'content': a.get('content_hash'),
                              'body': _normalized_text(a.get('body') or a.get('summary'))} for a in articles],
                 'watchlist': watchlist_content}
    revision, prior = _event_revision(company['id'], catalyst_id, signature, db_path)
    return {'company': company, 'catalyst_id': catalyst_id,
            'evidence_revision': revision, 'signature': signature,
            'package_id': None, 'articles': articles, 'catalyst': catalyst, 'root_event_ids': [],
            'observed_at': at, 'published_at': max((a['event_time'] for a in articles), default=source_time or at),
            'watchlist': watchlist_content, 'predecessor_revision': _predecessor_revision(prior, revision),
            'change_kind': 'material' if prior else 'initial', 'legacy_high_confidence_articles': len(articles),
            'minimum_legacy_articles': max(1, AgentPipelineSettings.from_env().minimum_high_confidence_company_articles)}


def discover_company_events(*, sector_symbols=None, policy=None, at=None, db_path=None):
    policy, at = policy or DiscoveryPolicy.from_env(), timestamp(at or utc_now())
    if policy.mode == 'disabled':
        return {'status': 'disabled', 'queue_ids': []}
    initialize_database(db_path=db_path)
    try:
        with research_session_lock(db_path, 'company-discovery'):
            return _discover_interested_companies(sector_symbols, policy, at, db_path)
    except ResearchSessionBusy:
        return {'status': 'busy', 'queue_ids': [], 'scanned_company_ids': []}


def _discover_interested_companies(sector_symbols, policy, at, db_path):
    if sector_symbols is not None:
        request_pipeline_evidence(sector_symbols, scope='discovery_pipeline', at=at, db_path=db_path)
    sector_symbols = sector_symbols or ()
    positions, watchlist = reconcile_company_interest(
        at=at, lifetime_seconds=CompanyEvidencePolicy.from_env().stale_seconds, db_path=db_path)
    companies = due_discovery_checks(at=at, limit=policy.scan_limit, db_path=db_path)
    with closing(get_connection(db_path)) as conn:
        pipeline_ids = {r[0] for r in conn.execute('''SELECT company_id FROM company_interest
            WHERE source IN ('pipeline_selection','opportunist') AND julianday(selected_at)<=julianday(?)
            AND julianday(expires_at)>julianday(?)''', (at, at))}
    sector_symbols = {c['symbol'] for c in companies if c['id'] in pipeline_ids} | {s.upper() for s in sector_symbols}
    queued, errors, waiting = [], [], []
    for company in companies:
        next_at = (datetime.fromisoformat(at) + timedelta(seconds=policy.rescan_seconds)).isoformat()
        dependency, reason = None, 'inputs_checked'
        try:
            events = company_discovery_events(company, at=at, policy=policy, db_path=db_path)
            if not events and (company['symbol'] in sector_symbols or company['id'] in watchlist):
                articles = sector_support_articles(company['id'], at=at, stale_seconds=policy.stale_seconds, db_path=db_path)
                if articles or company['id'] in watchlist:
                    events = [_sector_event(company, at=at, policy=policy, db_path=db_path)]
            dependency = _collection_dependency(company, at, db_path)
            if dependency:
                waiting.append({'company_id': company['id'], 'refresh_request_id': dependency})
                reason = 'missing_or_stale_primary_evidence'
            for event in events:
                queued.extend(_enqueue_routes(event, positions, watchlist, sector_symbols, at, policy, db_path))
        except Exception as exc:
            reason = 'discovery_error:' + type(exc).__name__
            errors.append({'company_id': company['id'], 'reason': type(exc).__name__})
            next_at = (datetime.fromisoformat(at) + timedelta(seconds=policy.retry_seconds)).isoformat()
        finish_discovery_check(company, at=at, next_at=next_at, reason=reason, dependency=dependency, db_path=db_path)
        mark_discovery_scanned(company['id'], at=at, lane='events', db_path=db_path)
    return {'status': 'shadow', 'queue_ids': sorted(set(queued)), 'waiting_for_evidence': waiting,
            'scanned_company_ids': [c['id'] for c in companies], 'errors': errors}


def _collection_dependency(company, at, db_path):
    from agent_builders.company_evidence_payload import build_company_evidence_payload
    payload = build_company_evidence_payload(company['id'], as_of=at, db_path=db_path)
    if payload.get('freshness_at_read') == 'fresh' and payload.get('coverage_status') != 'unavailable':
        return None
    with closing(get_connection(db_path)) as conn, conn:
        row = conn.execute('''SELECT request_id FROM company_evidence_requests
            WHERE company_id=? AND status='pending' AND julianday(requested_at)<=julianday(?)
            ORDER BY requested_at LIMIT 1''', (company['id'], at)).fetchone()
        if row:
            return row[0]
        version = digest([payload.get('document_ids', []), payload.get('freshness_at_read', 'missing')])
        return enqueue_refresh(conn, company['id'], 'discovery', version, at=at)


def _enqueue_routes(event, positions, watchlist, sector_symbols, at, policy, db_path):
    company = event['company']
    expiry = (datetime.fromisoformat(event['published_at']) + timedelta(seconds=policy.stale_seconds)).isoformat()
    if expiry <= at:
        return []
    routes = [] if event['catalyst_id'].startswith('catalyst:sector-discovery:') else [('entry', 'direct_event')]
    if company['symbol'] in sector_symbols:
        routes.append(('entry', 'sector'))
    if company['id'] in watchlist:
        routes.append(('watchlist', 'watchlist'))
    if company['id'] in positions:
        routes.append(('position_review', 'position_review'))
    identities = []
    for purpose, origin in routes:
        item = DiscoveryQueueItem(company_id=company['id'], catalyst_id=event['catalyst_id'],
                                  evidence_revision=event['evidence_revision'], purpose=purpose, origin=origin,
                                  created_at=at, priority_components=priority_components(event, purpose, at=at))
        identities.append(enqueue_discovery_event(item, event, expires_at=expiry, db_path=db_path))
    return identities
