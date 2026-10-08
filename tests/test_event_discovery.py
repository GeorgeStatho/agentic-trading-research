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
from market_db_modules.bootstrap import initialize_database
from news_db_modules.bootstrap import initialize_database as initialize_news
from market_db_modules.discovery_queue import (
    claim_discovery_batch, discovery_queue_status, enqueue_discovery_event, finish_discovery_item,
    preview_discovery_queue, renew_discovery_lease,
)
from market_db_modules.discovery_sources import select_discovery_companies, mark_discovery_scanned
from research_foundation_contracts import DiscoveryQueueItem
from services.discovery_policy import DiscoveryPolicy
from services.event_discovery import discover_company_events, priority_components
from services.discovery_queue import process_discovery_queue, build_discovery_research_input, discovery_targets
from services.company_evidence_extraction import ingest_source
from services.company_evidence_packages import build_company_package
from services.company_evidence_policy import CompanyEvidencePolicy


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
        from services.event_discovery import company_discovery_events
        self.package(company={**COMPANY, 'id': 2, 'symbol': 'OTHER'})
        def load(company, **kwargs):
            if company['id'] == 1:
                raise ValueError('Invalid evidence')
            return company_discovery_events(company, **kwargs)
        with patch('services.event_discovery.company_discovery_events', side_effect=load):
            self.discover()
        self.assertEqual(discovery_queue_status(db_path=self.db)[0]['symbol'], 'OTHER')

    def test_sector_only_route_uses_existing_support_setting(self):
        self.article()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('UPDATE news_articles SET primary_ticker=NULL')
            conn.execute("INSERT INTO company_opportunist_impacts(article_id,company_id,confidence,created_at) VALUES (1,1,'high',?)", (START,))
        with patch.dict('os.environ', {'PIPELINE_MIN_HIGH_CONFIDENCE_COMPANY_ARTICLES': '1'}):
            self.discover(sector_symbols=('NVDA',))
        self.assertEqual(self.process()['candidates'][0]['admission']['route'], 'sector')

    def test_guidance_reversion_is_a_new_material_revision(self):
        for offset, value in ((0, 100), (60, 90), (120, 100)):
            self.package(value, at=later(offset))
            self.discover(at=later(offset + 1))
        self.assertEqual(len(discovery_queue_status(db_path=self.db)), 3)

    def test_watchlist_update_queues_without_fabricated_evidence(self):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO strategist_company_summaries(company_id,decision,summary,updated_at) VALUES (1,'watchlist','Wait for results',?)", (START,))
        self.discover()
        self.assertEqual(self.claim()[0]['purpose'], 'watchlist')

    def test_changed_watchlist_rationale_creates_new_revision(self):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO strategist_company_summaries(company_id,decision,summary,updated_at) VALUES (1,'watchlist','Wait for results',?)", (START,))
        self.discover()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE strategist_company_summaries SET summary='Wait for guidance clarification',updated_at=?", (ENTRY,))
        self.discover(at=later(1))
        self.assertEqual(len(discovery_queue_status(db_path=self.db)), 2)

    def test_primary_package_has_persisted_provenance_root(self):
        self.package()
        self.discover()
        root = self.claim()[0]['event']['root_event_ids'][0]
        with closing(get_connection(self.db)) as conn:
            stored = conn.execute('SELECT verified FROM root_events WHERE root_event_id=?', (root,)).fetchone()
        self.assertEqual(stored[0], 1)

    def test_company_outside_ranked_sectors_reaches_research(self):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO sectors(id,sector_key,name) VALUES (2,'healthcare','Healthcare')")
            conn.execute("INSERT INTO industries(id,sector_id,industry_key,name) VALUES (2,2,'devices','Devices')")
            conn.execute('UPDATE companies SET industry_id=2 WHERE id=2')
        company = {**COMPANY, 'id': 2, 'symbol': 'OTHER', 'name': 'Outside'}
        self.package(company=company)
        self.discover(sector_symbols=('NVDA',))
        results = self.process()['candidates']
        self.assertTrue(any(row['symbol'] == 'OTHER' and row['admission']['allowed']
                            and row['research_input']['company']['sector_key'] == 'healthcare' for row in results))

    def test_ten_syndicated_copies_produce_one_queue_item(self):
        for index in range(1, 11):
            self.article(index, primary=index == 1)
            self.discover(at=later(index))
        self.assertEqual(len(discovery_queue_status(db_path=self.db)), 1)

    def test_new_guidance_revision_produces_new_item(self):
        self.package()
        self.discover()
        self.package(90, at=later(60))
        self.discover(at=later(61))
        self.assertEqual(len(discovery_queue_status(db_path=self.db)), 2)

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

    def test_existing_news_catalyst_reused_for_matching_primary_document(self):
        self.article()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE news_articles SET primary_source_url='https://issuer.test/release'")
        self.package()
        self.discover()
        self.assertEqual(self.claim()[0]['catalyst_id'], CATALYST)

    def test_separate_routes_share_entry_item(self):
        self.package()
        self.discover(sector_symbols=('NVDA',))
        self.assertEqual(self.claim()[0]['origins'], ['direct_event', 'sector'])

    def test_position_review_and_watchlist_are_separate_purposes(self):
        self.package()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO position_theses(symbol,option_symbol,opened_at) VALUES ('NVDA','NVDA-OPTION',?)", (START,))
            conn.execute("INSERT INTO strategist_company_summaries(company_id,decision,updated_at) VALUES (1,'watchlist',?)", (START,))
        self.discover()
        self.assertEqual({row['purpose'] for row in discovery_queue_status(db_path=self.db)}, {'entry', 'position_review', 'watchlist'})

    def test_primary_news_without_opportunist_support_reaches_research(self):
        self.article()
        self.discover()
        self.assertEqual(self.process()['candidates'][0]['admission']['route'], 'primary_news')

    def test_weak_coverage_is_deferred(self):
        self.article(primary=False)
        self.discover()
        self.process()
        self.assertEqual(discovery_queue_status(db_path=self.db)[0]['status'], 'deferred')

    def test_correction_of_existing_root_creates_revision(self):
        self.article()
        self.discover()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE news_articles SET title='Corrected earnings guidance' WHERE id=1")
            conn.execute("UPDATE news_article_content SET body='Guidance is 90 million.' WHERE article_id=1")
            from market_db_modules.company_interest import signal_company_news
            signal_company_news(conn, 1, at=later(1))
        self.discover(at=later(1))
        self.assertEqual(self.claim(at=later(2))[0]['event']['change_kind'], 'correction')

    def test_priority_treats_bullish_and_bearish_events_symmetrically(self):
        event = {'catalyst': {'catalyst_type': 'earnings', 'direction': 'positive'}, 'package_id': 'package', 'published_at': START}
        bullish = priority_components(event, 'entry', at=ENTRY)
        event['catalyst']['direction'] = 'negative'
        self.assertEqual(priority_components(event, 'entry', at=ENTRY), bullish)

    def test_shadow_candidate_contains_saved_observation(self):
        self.package()
        self.discover()
        result = self.process()['candidates'][0]
        self.assertTrue(result['observation_id'].startswith('observation:'))

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
    def test_older_revision_cannot_replace_newer_pending_work(self):
        self.queued_item(at=later(100), revision='new')
        with self.assertRaises(ValueError):
            self.queued_item(revision='old')

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

    def test_stale_worker_cannot_renew_lease(self):
        self.queued_item()
        old = self.claim()[0]
        with self.assertRaises(ValueError):
            renew_discovery_lease(old['queue_id'], old['lease_token'], at=later(121), seconds=30, db_path=self.db)

    def test_renewed_lease_prevents_early_reclaim(self):
        self.queued_item()
        item = self.claim()[0]
        renew_discovery_lease(item['queue_id'], item['lease_token'], at=later(60), seconds=120, db_path=self.db)
        self.assertEqual(self.claim(at=later(121)), [])

    def test_retry_is_not_claimable_before_backoff(self):
        self.queued_item()
        item = self.claim()[0]
        finish_discovery_item(item['queue_id'], item['lease_token'], status='deferred', reason='provider_failure',
                              at=ENTRY, policy=self.policy, db_path=self.db)
        self.assertEqual(self.claim(at=later(59)), [])

    def test_retry_exhaustion_is_terminal(self):
        self.queued_item()
        for offset in (0, 61, 182):
            item = self.claim(at=later(offset))[0]
            finish_discovery_item(item['queue_id'], item['lease_token'], status='deferred', reason='missing_evidence',
                                  at=later(offset), policy=self.policy, db_path=self.db)
        self.assertEqual(discovery_queue_status(db_path=self.db)[0]['status'], 'failed')

    def test_expired_items_are_not_claimed(self):
        self.queued_item()
        self.claim(at=later(86401))
        self.assertEqual(discovery_queue_status(db_path=self.db)[0]['status'], 'expired')

    def test_new_revision_invalidates_running_lease(self):
        self.queued_item()
        old = self.claim()[0]
        self.queued_item(revision='revision-2', at=later(1))
        with self.assertRaises(ValueError):
            finish_discovery_item(old['queue_id'], old['lease_token'], status='completed', reason='done',
                                  at=later(2), policy=self.policy, db_path=self.db)

    def test_restart_keeps_pending_work(self):
        identity = self.queued_item()
        initialize_database(db_path=self.db)
        self.assertEqual(self.claim()[0]['queue_id'], identity)

    def test_discovery_worker_never_claims_position_reviews(self):
        self.queued_item(purpose='position_review', components={'review_urgency': 100})
        self.queued_item(components={'materiality': 1})
        self.assertEqual([item['purpose'] for item in self.claim()], ['entry'])

    def test_reserved_direct_slot_beats_sector_priority(self):
        self.queued_item(origin='sector', purpose='watchlist', components={'materiality': 100})
        self.queued_item(origin='direct_event', components={'materiality': 1})
        policy = replace(self.policy, batch_size=2, direct_slots=1)
        self.assertEqual(self.claim(policy=policy)[0]['purpose'], 'entry')

    def test_waiting_time_prevents_general_lane_starvation(self):
        self.queued_item(origin='sector', purpose='watchlist', components={'materiality': 1}, at=START)
        self.queued_item(components={'materiality': 10})
        policy = replace(self.policy, batch_size=1, direct_slots=0)
        # Extend the test item's lifetime beyond its one-day fixture default.
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('UPDATE discovery_queue_details SET expires_at=?', (later(86400),))
        self.assertEqual(self.claim(policy=policy)[0]['purpose'], 'watchlist')

    def test_network_work_can_open_another_writer_after_claim(self):
        self.queued_item()
        self.claim()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("UPDATE companies SET rating='test' WHERE id=1")
        self.assertTrue(True)


class DiscoveryScanTests(DiscoveryFixture):
    def test_scheduled_earnings_get_priority_scan_capacity(self):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO companies(id,industry_id,symbol,name) VALUES (3,1,'SCHEDULED','Scheduled')")
            from market_db_modules.company_interest import register_interest
            register_interest(conn, 3, 'fixture', 'selection', at=START, lifetime_seconds=604800)
        source = replace(guidance(), scheduled_events=({'scheduled_at': later(86400), 'confirmed': True},))
        company = {**COMPANY, 'id': 3, 'symbol': 'SCHEDULED'}
        doc, _ = ingest_source(company, source, START, db_path=self.db)
        build_company_package(company, [doc], START, CompanyEvidencePolicy(), db_path=self.db)
        policy = replace(self.policy, symbols=('*',), scan_limit=2)
        self.assertEqual([c['symbol'] for c in select_discovery_companies(policy, at=ENTRY, db_path=self.db)], ['NVDA', 'SCHEDULED'])

    def test_discovery_universe_does_not_trigger_unselected_company_collection(self):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('DELETE FROM company_interest')
        from services.company_evidence_service import collect_company_evidence
        adapter = Mock()
        with patch('services.discovery_policy.DiscoveryPolicy.from_env', return_value=replace(self.policy, symbols=('*',))), \
             patch('services.company_evidence_service.SECAdapter', return_value=adapter), \
             patch.dict('os.environ', {'SEC_USER_AGENT': 'Fixture operator@example.test', 'COMPANY_EVIDENCE_SYMBOLS': 'NVDA'}):
            result = collect_company_evidence(policy=CompanyEvidencePolicy(enabled=True, companies_per_cycle=1),
                                              clock=lambda: datetime.fromisoformat(ENTRY), db_path=self.db)
        self.assertEqual((result['status'], adapter.issuer_map.call_count), ('idle', 0))

    def test_rotating_scan_visits_low_priority_company(self):
        policy = replace(self.policy, scan_limit=1)
        first = select_discovery_companies(policy, at=ENTRY, db_path=self.db)[0]
        mark_discovery_scanned(first['id'], at=ENTRY, db_path=self.db)
        self.assertEqual(select_discovery_companies(policy, at=later(1), db_path=self.db)[0]['symbol'], 'OTHER')

    def test_unconfigured_universe_is_not_assumed_tradable(self):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('DELETE FROM company_interest')
        self.assertEqual(select_discovery_companies(replace(self.policy, symbols=()), at=ENTRY, db_path=self.db), [])

    def test_live_mode_is_not_available(self):
        with self.assertRaises(ValueError):
            replace(self.policy, mode='live')

    def test_at_least_one_general_slot_is_required(self):
        with self.assertRaises(ValueError):
            replace(self.policy, batch_size=2, direct_slots=2)

    def test_read_only_target_preview_marks_shadow(self):
        self.package()
        self.discover()
        self.assertTrue(discovery_targets(policy=self.policy, at=ENTRY, db_path=self.db)[0]['discovery_shadow'])


class ShadowBoundaryTests(DiscoveryFixture):
    def test_full_stack_separates_discovery_from_live_manager_output(self):
        from agentCallers import main as agent_main
        from services.discovery_queue import run_discovery_shadow_cycle
        self.package(company={**COMPANY, 'id': 2, 'symbol': 'OTHER'})
        callback = Mock()
        def shadow_cycle(**kwargs):
            return run_discovery_shadow_cycle(**kwargs, policy=self.policy, clock=lambda: ENTRY, db_path=self.db)
        with patch.object(agent_main, 'run_agent_pipeline_from_existing_data', return_value={'sectors': []}), \
             patch.object(agent_main, 'build_market_regime_snapshot', return_value={}), \
             patch('services.discovery_queue.run_discovery_shadow_cycle', side_effect=shadow_cycle):
            result = agent_main.run_full_agent_stack_from_existing_data(on_manager_result=callback)
        self.assertEqual((result['pipeline']['discovery_shadow']['candidates'][0]['symbol'], result['manager'], callback.call_count),
                         ('OTHER', [], 0))
    def test_main_admission_accepts_primary_evidence_without_opportunist(self):
        from agentCallers import main as agent_main
        self.package()
        self.discover()
        item = self.claim()[0]
        payload = build_discovery_research_input(item, db_path=self.db)
        context = {'event': item['event'], 'evidence': payload['company_evidence'], 'at': ENTRY, 'policy': self.policy}
        contexts = {}
        with patch.object(agent_main, 'get_company_opportunist_summary', side_effect=AssertionError('Legacy gate should not run')):
            allowed, _ = agent_main._filter_company_symbols_by_high_confidence_support(
                ['NVDA'], minimum_high_confidence_articles=2, max_age_days=7,
                candidate_context_by_symbol=contexts, discovery_context_by_symbol={'NVDA': context})
        self.assertEqual((allowed, contexts['NVDA']['discovery_shadow']), (['NVDA'], True))

    def test_shadow_candidate_never_reaches_models_or_broker_callback(self):
        from agentCallers import main as agent_main
        callback = Mock()
        with patch.object(agent_main, 'decide_company_purchase', side_effect=AssertionError('No model call')), \
             patch.object(agent_main, 'decide_company_option_position', side_effect=AssertionError('No manager call')):
            agent_main._run_strategist_and_manager(['OTHER'], market_regime={},
                company_context_by_symbol={'OTHER': {'discovery_shadow': True}}, on_manager_result=callback)
        callback.assert_not_called()

    def test_persisted_discovery_identity_is_never_order_eligible(self):
        from services.order_candidates import OrderCandidateBuilder
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
