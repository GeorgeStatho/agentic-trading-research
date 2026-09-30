"""Bounded shadow consumer. It produces frozen research inputs, never manager results."""
from datetime import datetime, timezone
from uuid import uuid4

from db_common import DB_PATH
from market_db_modules.discovery_candidates import persist_discovery_candidate
from market_db_modules.discovery_queue import claim_discovery_batch, finish_discovery_item, preview_discovery_queue
from research_foundation_contracts import timestamp
from services.discovery_admission import evaluate_discovery_admission
from services.discovery_policy import DiscoveryPolicy


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def discovery_targets(*, policy=None, at=None, db_path=DB_PATH):
    policy = policy or DiscoveryPolicy.from_env()
    if policy.mode == 'disabled':
        return []
    items = preview_discovery_queue(at=at or utc_now(), limit=policy.batch_size,
                                    aging_per_hour=policy.aging_per_hour, db_path=db_path)
    return [{**item['event']['company'], 'company_id': item['company_id'], 'queue_id': item['queue_id'],
             'purpose': item['purpose'], 'priority_components': item['priority_components'],
             'source_groups': item['origins'], 'discovery_shadow': True} for item in items]


def build_discovery_research_input(item, *, db_path=DB_PATH):
    from agent_builders.company_evidence_payload import build_company_evidence_payload
    event = item['event']
    evidence = (build_company_evidence_payload(item['company_id'], package_id=event['package_id'],
                                              as_of=event['observed_at'], db_path=db_path)
                if event.get('package_id') else {'coverage_status': 'unavailable', 'facts': [], 'gaps': ['primary_package_unavailable']})
    articles = [{'article_id': article['id'], 'title': article['title'], 'body': article.get('body'),
                 'published_at': timestamp(article['event_time']), 'source_url': article['source_url'],
                 'root_event_id': article.get('root_event_id')} for article in event['articles']]
    return {'company': {**event['company'], 'company_id': item['company_id']},
            'company_evidence': evidence, 'evidence': articles, 'catalysts': [event['catalyst']],
            'watchlist_context': event.get('watchlist', {}),
            'research_predecessor_revision': event.get('predecessor_revision')}


def process_discovery_queue(*, policy=None, owner=None, clock=utc_now, version=None, db_path=DB_PATH):
    policy = policy or DiscoveryPolicy.from_env()
    if policy.mode == 'disabled':
        return {'status': 'disabled', 'candidates': []}
    from market_db_modules.bootstrap import initialize_database
    initialize_database(db_path=db_path)
    owner = owner or 'discovery-worker:' + uuid4().hex
    if version is None:
        from services.pipeline_version import build_pipeline_version
        version = build_pipeline_version()
    items = claim_discovery_batch(owner, at=clock(), policy=policy, db_path=db_path)
    results, failures = [], []
    for item in items:
        try:
            payload = build_discovery_research_input(item, db_path=db_path)
            admission = evaluate_discovery_admission(item['event'], payload['company_evidence'], at=clock(), policy=policy)
            result = persist_discovery_candidate(item, payload, admission, version, at=clock(), db_path=db_path)
            finish_discovery_item(item['queue_id'], item['lease_token'],
                                  status='completed' if result['admission']['allowed'] or result.get('research_reused') else 'deferred',
                                  reason='covered_by_existing_research' if result.get('research_reused') else result['admission']['reason'], at=clock(), policy=policy, db_path=db_path)
            results.append(result)
        except Exception as exc:
            failures.append({'queue_id': item['queue_id'], 'reason': type(exc).__name__})
            try:
                finish_discovery_item(item['queue_id'], item['lease_token'], status='deferred',
                                      reason='worker_error:' + type(exc).__name__, at=clock(), policy=policy, db_path=db_path)
            except ValueError:
                # A superseding revision or reclaimed lease belongs to the next worker.
                pass
    return {'status': 'shadow', 'candidates': results, 'failures': failures}


def run_discovery_shadow_cycle(*, sector_symbols=(), policy=None, clock=utc_now, db_path=DB_PATH):
    from services.event_discovery import discover_company_events
    policy = policy or DiscoveryPolicy.from_env()
    if policy.mode == 'disabled':
        return {'status': 'disabled', 'candidates': []}
    discovery = discover_company_events(sector_symbols=sector_symbols, policy=policy, at=clock(), db_path=db_path)
    result = {**process_discovery_queue(policy=policy, clock=clock, db_path=db_path), 'discovery': discovery}
    from services.research_orchestrator import process_research_queue
    result['research'] = process_research_queue(clock=clock, limit=policy.batch_size, db_path=db_path)
    from services.research_thesis import process_research_theses
    result['theses'] = process_research_theses(clock=clock, limit=policy.batch_size, db_path=db_path)
    return result
