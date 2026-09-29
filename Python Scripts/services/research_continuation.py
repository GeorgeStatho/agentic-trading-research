"""Continue a deferred investigation with a new observation, never a revised past."""
from contextlib import closing
from copy import deepcopy
import json

from db_common import DB_PATH, get_connection
from market_db_modules.company_evidence import get_company_fact, get_evidence_package, record_evidence_package
from market_db_modules.foundation_store import canonical, digest
from market_db_modules.research_foundation import get_discovery_item, get_research_session
from market_db_modules.research_observations import bind_candidate_observation, record_research_observation
from market_db_modules.research_runtime import load_research_progress, research_event_superseded
from research_foundation_contracts import EvidencePackage, ResearchObservation


def prepare_research_continuations(*, limit, db_path=DB_PATH):
    """Idempotent recovery also handles a collector restart after saving its package."""
    with closing(get_connection(db_path)) as conn:
        requests = [dict(row) for row in conn.execute('''SELECT r.* FROM company_evidence_requests r
            JOIN research_sessions s ON s.session_id=r.parent_session_id
            LEFT JOIN research_evidence_continuations c ON c.request_id=r.request_id
            WHERE r.status='completed' AND r.package_id IS NOT NULL
            AND s.status='deferred_missing_evidence' AND c.request_id IS NULL
            AND r.reason IS NULL ORDER BY r.completed_at,r.request_id LIMIT ?''', (limit,))]
    failures = []
    for request in requests:
        try:
            reason = _prepare_continuation(request, db_path)
        except Exception as exc:
            failures.append({'refresh_request_id': request['request_id'], 'reason': type(exc).__name__})
            continue
        if reason:
            with closing(get_connection(db_path)) as conn, conn:
                conn.execute('UPDATE company_evidence_requests SET reason=? WHERE request_id=? AND reason IS NULL',
                             (reason, request['request_id']))
    return failures


def _prepare_continuation(request, db_path):
    parent = get_research_session(request['parent_session_id'], db_path=db_path)
    if research_event_superseded(parent['queue_id'], db_path=db_path):
        return 'event_superseded'
    progress = load_research_progress(parent['session_id'], db_path=db_path)
    package = get_evidence_package(request['package_id'], db_path=db_path)
    old = get_evidence_package(parent['final_package_id'], db_path=db_path)
    if package['company_id'] != old['company_id']:
        raise ValueError('Refresh package belongs to another company')
    if package['information_cutoff_time'] <= old['information_cutoff_time']:
        return 'no_later_evidence_snapshot'
    # A newly stamped package over identical archived inputs must not buy another model run.
    source_facts = {identity for identity in package['fact_ids']
                    if get_company_fact(identity, db_path=db_path)['kind'] != 'derived'}
    if (set(package['document_ids']) <= set(old['document_ids'])
            and source_facts <= set(old['fact_ids'])
            and package['freshness_status'] == old['freshness_status']):
        return 'no_new_evidence'
    item = get_discovery_item(parent['queue_id'], db_path=db_path)
    with closing(get_connection(db_path)) as conn:
        row = conn.execute('''SELECT payload_json,payload_hash FROM discovery_events
            WHERE company_id=? AND catalyst_id=? AND evidence_revision=?''',
                           (item['company_id'], item['catalyst_id'], item['evidence_revision'])).fetchone()
    event = json.loads(row['payload_json'])
    if digest(event) != row['payload_hash']:
        raise ValueError('Corrupt discovery event')
    fields = {k: v for k, v in package.items() if k != 'package_id'}
    fields.update(catalyst_id=item['catalyst_id'], event_revision=item['evidence_revision'])
    package_id = 'package:research-refresh:' + digest(fields)
    record_evidence_package(EvidencePackage(package_id=package_id, **fields), db_path=db_path)
    from agent_builders.company_evidence_payload import build_company_evidence_payload
    from services.discovery_admission import evaluate_discovery_admission
    from services.discovery_policy import DiscoveryPolicy
    payload = deepcopy(progress['initial_input']['research_input'])
    cutoff = package['information_cutoff_time']
    payload['company_evidence'] = build_company_evidence_payload(item['company_id'], package_id=package_id,
                                                                as_of=cutoff, db_path=db_path)
    lineage = {'parent_session_id': parent['session_id'], 'refresh_request_id': request['request_id'],
               'previous_observation_id': progress['initial_input']['observation_id']}
    payload['research_continuation'] = lineage
    payload['research_predecessor_revision'] = item['evidence_revision']
    observation_id = record_research_observation(ResearchObservation(
        company_id=item['company_id'], symbol=progress['initial_input']['symbol'],
        catalyst_id=item['catalyst_id'], event_revision=item['evidence_revision'],
        information_cutoff_time=cutoff, source_snapshot=payload), db_path=db_path)
    identity = 'research:refresh:' + digest(request['request_id'])
    candidate_id = 'discovery:refresh:' + digest(request['request_id'])
    candidate = {**progress['initial_input'], 'candidate_id': candidate_id, 'observation_id': observation_id,
                 'research_input': payload, 'research_session_id': identity, 'continuation': lineage,
                 'admission': evaluate_discovery_admission(event, payload['company_evidence'],
                                                          at=cutoff, policy=DiscoveryPolicy.from_env())}
    version = candidate['pipeline_version']
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT 1 FROM research_evidence_continuations WHERE request_id=?',
                        (request['request_id'],)).fetchone():
            return None
        from market_db_modules.research_work import claim_research_work
        owner = claim_research_work(conn, candidate, item['company_id'], item['catalyst_id'], identity, at=cutoff)
        if owner != identity:
            return 'research_already_scheduled:' + owner
        candidate.update(research_owner_session_id=owner, research_reused=False)
        conn.execute('''INSERT INTO candidates
            (candidate_id,symbol,created_at,catalyst_ids_json,article_ids_json,pipeline_version_json)
            VALUES (?,?,?,?,?,?)''', (candidate_id, candidate['symbol'], cutoff, canonical([item['catalyst_id']]),
                                    canonical([a['article_id'] for a in payload.get('evidence', [])]), canonical(version)))
        bind_candidate_observation(conn, candidate_id, observation_id)
        conn.execute('INSERT INTO research_evidence_continuations VALUES (?,?,?,?,?,?)',
                     (request['request_id'], parent['session_id'], identity, canonical(candidate), digest(candidate), cutoff))
    return None
