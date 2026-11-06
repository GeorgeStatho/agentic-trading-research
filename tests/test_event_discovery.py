"""Phase 2 acceptance: provenance, fairness, lease recovery and shadow isolation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import Mock, patch

from tests.research_foundation_fixtures import database, START, ENTRY, CATALYST
from tests.test_company_evidence import guidance, COMPANY
from db_common import get_connection
from news_db_modules.bootstrap import initialize_database as initialize_news
from market_db_modules.discovery_queue import (
    claim_discovery_batch,
    discovery_queue_status,
    enqueue_discovery_event,
    finish_discovery_item,
)
from market_db_modules.discovery_sources import select_discovery_companies
from research_foundation_contracts import DiscoveryQueueItem
from services.discovery.discovery_policy import DiscoveryPolicy
from services.discovery.event_discovery import discover_company_events
from services.discovery.discovery_queue import process_discovery_queue, build_discovery_research_input
from services.discovery.company_evidence_extraction import ingest_source
from services.discovery.company_evidence_packages import build_company_package
from services.discovery.company_evidence_policy import CompanyEvidencePolicy


def later(seconds, at=ENTRY):
    return (datetime.fromisoformat(at) + timedelta(seconds=seconds)).isoformat()


class DiscoveryFixture(unittest.TestCase):
    interested_symbols = ('NVDA', 'OTHER')

    def setUp(self):
        self.db = database(self)
        initialize_news(db_path=self.db)
        self.policy = DiscoveryPolicy(mode='shadow', symbols=('NVDA', 'OTHER'))
        from market_db_modules.company_interest import register_interest
        with closing(get_connection(self.db)) as conn, conn:
            for row in conn.execute('SELECT id,symbol FROM companies').fetchall():
                if row['symbol'] in self.interested_symbols:
                    register_interest(conn, row['id'], 'fixture', 'selection', at=START, lifetime_seconds=604800)


    def package(self, low=100, *, at=START, company=None):
        company = company or COMPANY
        source = replace(guidance(low, low + 10, at=at), metadata={'cik': company['cik']})
        document_id, _ = ingest_source(company, source, at, db_path=self.db)
        return build_company_package(company, [document_id], at, CompanyEvidencePolicy(), db_path=self.db)

    def article(self, index=1, *, body='Revenue guidance raised to 110 million.', root='root:earnings', primary=True):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('''INSERT INTO news_articles
                (id,source,article_key,title,source_url,published_at,event_time,first_seen_time,
                 primary_ticker,catalyst_id,root_event_id,root_source_id,source_type,evidence_score,syndication_parent_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                         (index, 'issuer' if primary else 'wire', f'article-{index}', 'Fixture earnings guidance',
                          f'https://issuer.test/{index}', START, START, START, 'NVDA', CATALYST, root,
                          'url:https://issuer.test/release', 'company_release' if primary else 'wire_service', .9,
                          None if index == 1 else 1))
            conn.execute('INSERT INTO news_article_content(article_id,body) VALUES (?,?)', (index, body))
            conn.execute('INSERT INTO company_news_articles(company_id,article_id) VALUES (1,?)', (index,))
            from market_db_modules.company_interest import signal_discovery
            signal_discovery(conn, 1, 'news:' + str(index), body, at=START)


    def discover(self, *, at=ENTRY, sector_symbols=()):
        return discover_company_events(policy=self.policy, at=at, sector_symbols=sector_symbols, db_path=self.db)

    def queued_item(self, *, purpose='entry', origin='direct_event', revision='revision-1', components=None, at=ENTRY):
        with closing(get_connection(self.db)) as conn:
            catalyst = json.loads(conn.execute('SELECT state_json FROM catalysts WHERE catalyst_id=?', (CATALYST,)).fetchone()[0])
        event = {'company': {'id': 1, 'symbol': 'NVDA', 'name': 'Fixture'}, 'catalyst_id': CATALYST,
                 'evidence_revision': revision, 'package_id': None, 'articles': [], 'catalyst': catalyst,
                 'published_at': START, 'observed_at': at, 'change_kind': 'initial'}
        item = DiscoveryQueueItem(company_id=1, catalyst_id=CATALYST, evidence_revision=revision,
                                  purpose=purpose, origin=origin, created_at=at,
                                  priority_components=components or {'materiality': 10})
        return enqueue_discovery_event(item, event, expires_at=later(86400, at), db_path=self.db)

    def claim(self, *, at=ENTRY, owner='worker', policy=None):
        return claim_discovery_batch(owner, at=at, policy=policy or self.policy, db_path=self.db)

    def process(self, at=ENTRY):
        return process_discovery_queue(policy=self.policy, clock=lambda: at, db_path=self.db,
                                       version={'pipeline_version': 'discovery-test-v1'})


class DiscoveryAcceptanceTests(DiscoveryFixture):
    def test_bad_company_does_not_block_rest_of_scan(self):
        from services.discovery.event_discovery import company_discovery_events
        self.package(company={**COMPANY, 'id': 2, 'symbol': 'OTHER'})
        def load(company, **kwargs):
            if company['id'] == 1:
                raise ValueError('Invalid evidence')
            return company_discovery_events(company, **kwargs)
        with patch('services.discovery.event_discovery.company_discovery_events', side_effect=load):
            self.discover()
        self.assertEqual(discovery_queue_status(db_path=self.db)[0]['symbol'], 'OTHER')

    def test_ten_syndicated_copies_produce_one_queue_item(self):
        for index in range(1, 11):
            self.article(index, primary=index == 1)
            self.discover(at=later(index))
        self.assertEqual(len(discovery_queue_status(db_path=self.db)), 1)

    def test_material_revision_expires_unprocessed_old_item(self):
        self.package()
        self.discover()
        self.package(90, at=later(60))
        self.discover(at=later(61))
        self.assertEqual(discovery_queue_status(db_path=self.db)[0]['completion_reason'], 'superseded')

    def test_identical_refresh_does_not_enqueue_again(self):
        self.package()
        self.discover()
        self.package(at=later(60))
        self.discover(at=later(61))
        self.assertEqual(len(discovery_queue_status(db_path=self.db)), 1)

    def test_weak_coverage_is_deferred(self):
        self.article(primary=False)
        self.discover()
        self.process()
        self.assertEqual(discovery_queue_status(db_path=self.db)[0]['status'], 'deferred')

    def test_shadow_processing_does_not_duplicate_completed_candidates(self):
        self.package()
        self.discover()
        self.process()
        self.assertEqual(self.process()['candidates'], [])

    def test_payload_uses_frozen_event_after_new_collection(self):
        self.package()
        self.discover()
        item = self.claim()[0]
        self.package(90, at=later(60))
        payload = build_discovery_research_input(item, db_path=self.db)
        self.assertEqual([f['value'] for f in payload['company_evidence']['facts'] if f['kind'] == 'guidance'], [100])


class QueueLeaseTests(DiscoveryFixture):
    def test_two_workers_cannot_claim_same_item(self):
        self.queued_item()
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(lambda owner: self.claim(owner=owner), ['one', 'two']))
        self.assertEqual(sum(len(batch) for batch in claims), 1)

    def test_crashed_worker_lease_is_reclaimed(self):
        self.queued_item()
        first = self.claim()[0]
        recovered = self.claim(at=later(self.policy.lease_seconds + 1), owner='recovery')[0]
        self.assertNotEqual(recovered['lease_token'], first['lease_token'])

    def test_stale_worker_cannot_complete_reclaimed_item(self):
        self.queued_item()
        old = self.claim()[0]
        self.claim(at=later(121), owner='new')
        with self.assertRaises(ValueError):
            finish_discovery_item(old['queue_id'], old['lease_token'], status='completed', reason='done',
                                  at=later(122), policy=self.policy, db_path=self.db)

    def test_retry_exhaustion_is_terminal(self):
        self.queued_item()
        for offset in (0, 61, 182):
            item = self.claim(at=later(offset))[0]
            finish_discovery_item(item['queue_id'], item['lease_token'], status='deferred', reason='missing_evidence',
                                  at=later(offset), policy=self.policy, db_path=self.db)
        self.assertEqual(discovery_queue_status(db_path=self.db)[0]['status'], 'failed')


class DiscoveryScanTests(DiscoveryFixture):
    def test_unconfigured_universe_is_not_assumed_tradable(self):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('DELETE FROM company_interest')
        self.assertEqual(select_discovery_companies(replace(self.policy, symbols=()), at=ENTRY, db_path=self.db), [])


class ShadowBoundaryTests(DiscoveryFixture):
    def test_full_stack_separates_discovery_from_live_manager_output(self):
        from agentCallers import decision_pipeline as agent_main
        from services.discovery.discovery_queue import run_discovery_shadow_cycle
        self.package(company={**COMPANY, 'id': 2, 'symbol': 'OTHER'})
        callback = Mock()
        def shadow_cycle(**kwargs):
            return run_discovery_shadow_cycle(**kwargs, policy=self.policy, clock=lambda: ENTRY, db_path=self.db)
        with patch.object(agent_main, 'run_agent_pipeline_from_existing_data', return_value={'sectors': []}), \
             patch.object(agent_main, 'build_market_regime_snapshot', return_value={}), \
             patch('services.discovery.discovery_queue.run_discovery_shadow_cycle', side_effect=shadow_cycle):
            result = agent_main.run_full_agent_stack_from_existing_data(on_manager_result=callback)
        self.assertEqual((result['pipeline']['discovery_shadow']['candidates'][0]['symbol'], result['manager'], callback.call_count),
                         ('OTHER', [], 0))
    def test_shadow_candidate_never_reaches_models_or_broker_callback(self):
        from agentCallers import decision_pipeline as agent_main
        callback = Mock()
        with patch.object(agent_main, 'decide_company_purchase', side_effect=AssertionError('No model call')), \
             patch.object(agent_main, 'decide_company_option_position', side_effect=AssertionError('No manager call')):
            agent_main._run_strategist_and_manager(['OTHER'], market_regime={},
                company_context_by_symbol={'OTHER': {'discovery_shadow': True}}, on_manager_result=callback)
        callback.assert_not_called()

    def test_persisted_discovery_identity_is_never_order_eligible(self):
        from services.trading.order_candidates import OrderCandidateBuilder
        result = {'candidate_id': 'discovery:fixture', 'decision': 'call', 'confidence': 'high',
                  'entry_gate': {'allowed': True}, 'market_regime_gate': {'allowed': True}}
        self.assertFalse(OrderCandidateBuilder()._is_trade_eligible(result))

    def test_worker_losing_lease_cannot_persist_candidate(self):
        from market_db_modules.discovery_candidates import persist_discovery_candidate
        self.package()
        self.discover()
        item = self.claim()[0]
        payload = build_discovery_research_input(item, db_path=self.db)
        self.claim(at=later(121), owner='replacement')
        with self.assertRaises(ValueError):
            persist_discovery_candidate(item, payload, {'allowed': True, 'reason': 'ready'},
                                        {'pipeline_version': 'test'}, at=later(122), db_path=self.db)

    def test_retry_after_persist_before_acknowledgement_is_idempotent(self):
        from market_db_modules.discovery_candidates import persist_discovery_candidate
        self.package()
        self.discover()
        item = self.claim()[0]
        payload = build_discovery_research_input(item, db_path=self.db)
        first = persist_discovery_candidate(item, payload, {'allowed': True, 'reason': 'ready'},
                                            {'pipeline_version': 'test'}, at=ENTRY, db_path=self.db)
        self.assertEqual(self.process(at=later(121))['candidates'][0]['candidate_id'], first['candidate_id'])


if __name__ == '__main__':
    unittest.main()
