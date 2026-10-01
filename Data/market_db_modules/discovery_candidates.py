"""Persist research-only candidates atomically with their queue lease check."""
from contextlib import closing
import json

from db_common import DB_PATH, get_connection
from research_foundation_contracts import ResearchObservation
from .discovery_queue import require_discovery_lease
from .foundation_store import canonical, digest
from .research_observations import bind_candidate_observation, record_research_observation
from .evidence_refresh import enqueue_refresh
from .research_work import claim_research_work


def persist_discovery_candidate(item, payload, admission, version, *, at, db_path=DB_PATH):
    event = item['event']
    observation = ResearchObservation(company_id=item['company_id'], symbol=event['company']['symbol'],
                                     catalyst_id=item['catalyst_id'], event_revision=item['evidence_revision'],
                                     information_cutoff_time=event['observed_at'], source_snapshot=payload)
    observation_id = record_research_observation(observation, db_path=db_path)
    candidate_id = 'discovery:' + digest(item['queue_id'])
    result = {'candidate_id': candidate_id, 'queue_id': item['queue_id'], 'observation_id': observation_id,
              'symbol': event['company']['symbol'], 'purpose': item['purpose'], 'shadow': True,
              'admission': admission, 'research_input': payload, 'pipeline_version': version}
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        require_discovery_lease(conn, item['queue_id'], item['lease_token'], at)
        existing = conn.execute('SELECT * FROM discovery_shadow_candidates WHERE queue_id=?', (item['queue_id'],)).fetchone()
        if existing:
            stored = json.loads(existing['payload_json'])
            if digest(stored) != existing['payload_hash']:
                raise ValueError('Corrupt discovery candidate')
            return stored
        session_id = 'research:' + digest(item['queue_id'])
        owner = claim_research_work(conn, result, item['company_id'], item['catalyst_id'], session_id, at=at)
        result.update(research_owner_session_id=owner, research_reused=owner != session_id)
        conn.execute('''INSERT INTO candidates
            (candidate_id,symbol,created_at,catalyst_ids_json,article_ids_json,pipeline_version_json)
            VALUES (?,?,?,?,?,?)''', (candidate_id, event['company']['symbol'], at, canonical([item['catalyst_id']]),
                                    canonical([a['id'] for a in event['articles']]), canonical(version)))
        catalyst = event['catalyst']
        conn.execute('INSERT INTO candidate_catalysts VALUES (?,?,?,?,?)',
                     (candidate_id, item['catalyst_id'], catalyst['catalyst_family'], 1, canonical(catalyst)))
        bind_candidate_observation(conn, candidate_id, observation_id)
        envelope = {'candidate_id': candidate_id, 'stage': 'discovery_admission',
                    'snapshot_version': 'discovery-shadow-v1', 'information_cutoff_time': event['observed_at'],
                    'pipeline_version': version, 'observation_id': observation_id, 'payload': result}
        conn.execute('''INSERT INTO candidate_snapshots
            (candidate_id,stage,captured_at,information_cutoff_time,pipeline_version,snapshot_version,snapshot_hash,snapshot_json)
            VALUES (?,?,?,?,?,?,?,?)''', (candidate_id, envelope['stage'], at, event['observed_at'],
                                       version['pipeline_version'], envelope['snapshot_version'], digest(envelope), canonical(envelope)))
        enqueue_refresh(conn, item['company_id'], 'research_candidate', candidate_id, at=at)
        conn.execute('''INSERT INTO discovery_shadow_candidates VALUES (?,?,?,?,?,?,?,?,1)''',
                     (item['queue_id'], candidate_id, observation_id, 'ready' if admission['allowed'] else 'deferred',
                      admission['reason'], canonical(result), digest(result), at))
    return result
