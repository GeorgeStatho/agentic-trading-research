from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database
from news_db_modules.bootstrap import initialize_database as initialize_news
from news_db_modules.entity_articles import add_company_news_article
from news_db_modules.impacts import add_company_opportunist_impact
from news_db_modules.catalysts import backfill_catalysts, enrich_impact
from market_db_modules.candidate_lifecycle import record_candidate
from services.gates.catalyst_gates import evaluate_catalyst_gates, CatalystGatePolicy

NOW = datetime.now(timezone.utc)
VERSION = {'pipeline_version': 'fixture-v3'}
TITLE = 'Nvidia raises revenue guidance after quarterly results'


class CatalystTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name)/'test.db'
        initialize_database(db_path=self.db)
        initialize_news(db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO sectors(id, sector_key, name) VALUES (1, 'technology', 'Technology')")
            conn.execute("INSERT INTO industries(id, sector_id, industry_key, name) VALUES (1, 1, 'chips', 'Chips')")
            conn.execute("INSERT INTO companies(id, industry_id, symbol, name) VALUES (1, 1, 'NVDA', 'Nvidia')")

    def article(self, number, domain='reuters.com', **extra):
        return add_company_news_article(company_id=1, source=domain, article_key=str(number),
            title=extra.pop('title', TITLE), body=extra.pop('body', 'Reuters reports Nvidia raised revenue guidance.'),
            source_url=f'https://{domain}/story/{number}', published_at=extra.pop('published_at', (NOW-timedelta(days=2)).isoformat()),
            recency_score=1, source_reputation_score=.95, directness_score=.95, factuality_score=.95,
            db_path=self.db, **extra)

    def state(self, article_id=1):
        with closing(get_connection(self.db)) as conn, conn:
            return json.loads(conn.execute('SELECT c.state_json FROM catalysts c JOIN news_articles a '
                              'ON c.catalyst_id=a.catalyst_id WHERE a.id=?', (article_id,)).fetchone()[0])

    def candidate(self):
        with closing(get_connection(self.db)) as conn, conn:
            evidence = [dict(row) for row in conn.execute('SELECT * FROM news_articles')]
        return record_candidate('NVDA', VERSION, evidence=evidence, db_path=self.db)

    def test_primary_document_and_independent_reporting_have_two_roots(self):
        self.article(1, 'sec.gov', body='Company filed revised revenue guidance.')
        self.article(2)
        state = self.state()
        self.assertEqual(state['independent_root_count'], 2)
        self.assertTrue(state['primary_source_present'])
        self.assertEqual(state['sector'], 'technology')
        self.assertEqual(evaluate_catalyst_gates(state)['PROVENANCE']['reason_codes'], ['pass_independent_roots'])

    def test_primary_linked_republication_does_not_add_root(self):
        self.article(1, 'sec.gov', body='Filed guidance.')
        self.article(2, raw_json={'provenance': {'primary_source_url': 'https://sec.gov/story/1'}})
        self.assertEqual(self.state()['independent_root_count'], 1)
        self.assertEqual(evaluate_catalyst_gates(self.state())['PROVENANCE']['reason_codes'], ['pass_with_single_primary_root'])

    def test_repost_cannot_refresh_the_root_clock(self):
        self.article(1)
        before = self.state()['latest_root_event_time']
        self.article(2, 'finance.yahoo.com', published_at=NOW.isoformat())
        self.assertEqual(self.state()['latest_root_event_time'], before)
        self.assertEqual(self.state()['article_count'], 2)

    def test_future_evidence_is_excluded_from_candidate_state(self):
        self.article(1)
        second = self.article(2, 'newswire.example.com', body='Independent reporting')
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('UPDATE news_articles SET first_seen_time=? WHERE id=?', ((NOW+timedelta(days=1)).isoformat(), second))
        context = self.candidate()
        self.assertEqual(context['catalysts'][0]['independent_root_count'], 1)
        self.assertEqual(self.state()['independent_root_count'], 2)

    def test_impact_fields_are_derived_and_invalid_roots_rejected(self):
        article_id = self.article(1)
        impact_id = add_company_opportunist_impact(article_id, 1, raw_json={'reason': 'Forward expectations'}, db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            row = conn.execute('SELECT * FROM company_opportunist_impacts WHERE id=?', (impact_id,)).fetchone()
            self.assertEqual(row['catalyst_family'], 'guidance_raise')
            root = json.loads(row['root_event_ids_json'])[0]
            self.assertTrue(root.startswith('root:'))
            for roots in ([str(article_id)], ['root:invented'], [article_id], 'root:not-a-list'):
                with self.subTest(roots=roots), self.assertLogs('news_db_modules.catalysts', 'WARNING'):
                    enriched = enrich_impact({'root_event_ids': roots}, article_id, conn=conn)
                    self.assertEqual((enriched['root_event_ids'], enriched['rejected_root_event_ids']), ([root], roots))

    def test_a_wrong_root_claim_no_longer_aborts_saving_the_impact(self):
        article_id = self.article(1)
        with self.assertLogs('news_db_modules.catalysts', 'WARNING'):
            impact_id = add_company_opportunist_impact(article_id, 1, raw_json={'reason': 'Forward expectations',
                                                                                'root_event_ids': ['root:invented']}, db_path=self.db)
        with closing(get_connection(self.db)) as conn:
            stored = json.loads(conn.execute('SELECT raw_json FROM company_opportunist_impacts WHERE id=?', (impact_id,)).fetchone()[0])
        self.assertEqual(stored['rejected_root_event_ids'], ['root:invented'])

    def test_backfill_is_repeatable_and_preserves_root_ids(self):
        self.article(1)
        self.article(2, 'newswire.example.com', body='Independent interviews')
        before = self.state()
        first = backfill_catalysts(db_path=self.db)
        second = backfill_catalysts(db_path=self.db)
        self.assertEqual(first, second)
        self.assertEqual(self.state()['root_event_ids'], before['root_event_ids'])
        with closing(get_connection(self.db)) as conn, conn:
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])


class GateTests(unittest.TestCase):
    def state(self, **extra):
        return {'catalyst_id': 'catalyst:fixture', 'catalyst_family': 'analyst', 'independent_root_count': 1,
                'primary_source_present': False, 'evidence_score': .9, 'first_public_time': (NOW-timedelta(days=4)).isoformat(),
                'latest_root_event_time': None, 'expected_half_life_minutes': 1440, **extra}

    def test_missing_freshness_inputs_give_no_verdict_but_still_block_under_enforcement(self):
        for state in (self.state(expected_half_life_minutes=None), self.state(first_public_time=None)):
            for mode, allowed in (('shadow', True), ('enforce', False)):
                gate = evaluate_catalyst_gates(state, as_of=NOW, policy=CatalystGatePolicy(freshness_mode=mode))['FRESHNESS']
                self.assertIsNone(gate['would_allow'])
                self.assertIs(gate['allowed'], allowed)

    def test_decay_threshold_and_shadow_default(self):
        gate = evaluate_catalyst_gates(self.state(), as_of=NOW)['FRESHNESS']
        self.assertAlmostEqual(gate['metrics']['remaining_information_value'], .0625)
        self.assertFalse(gate['would_allow'])
        self.assertTrue(gate['allowed'])
        self.assertEqual(gate['metrics']['calibration'], 'research_prior')

    def test_bad_mode_is_not_silently_enforced(self):
        with patch.dict('os.environ', {'V2_FRESHNESS_MODE': 'typo'}), self.assertRaises(ValueError):
            CatalystGatePolicy.from_env()

    def test_enforcement_cannot_bypass_missing_candidate_state(self):
        from services.gates.catalyst_gates import catalyst_admission_allowed
        self.assertTrue(catalyst_admission_allowed(None, policy=CatalystGatePolicy()))
        self.assertFalse(catalyst_admission_allowed(None, policy=CatalystGatePolicy(freshness_mode='enforce')))
        self.assertFalse(catalyst_admission_allowed({'catalyst_gates': {}}, policy=CatalystGatePolicy(provenance_mode='enforce')))

    def test_primary_source_exception_replaces_legacy_article_count_only_when_enforced(self):
        from tests.test_agent_runner_main import agent_runner_main
        for mode, expected in (('shadow', []), ('enforce', ['NVDA'])):
            context = {'candidate_id': 'fixture', 'catalyst_gates': {'PROVENANCE': {
                'allowed': True, 'would_allow': True, 'enforcement_mode': mode,
                'reason_codes': ['pass_with_single_primary_root']}}}
            with patch('services.runtime.candidate_lifecycle.begin_candidate', return_value=context), \
                 patch('services.runtime.candidate_lifecycle.checkpoint'), patch('services.runtime.candidate_lifecycle.reject'), \
                 patch.object(agent_runner_main, 'get_company_opportunist_summary', return_value={
                     'company': {'symbol': 'NVDA'}, 'confidence_counts': {'high': 1}}):
                allowed, skipped = agent_runner_main._filter_company_symbols_by_high_confidence_support(
                    ['NVDA'], minimum_high_confidence_articles=2, max_age_days=5)
            self.assertEqual(allowed, expected)

    def test_provenance_rejection_blocks_even_when_legacy_article_count_passes(self):
        from tests.test_agent_runner_main import agent_runner_main
        context = {'candidate_id': 'fixture', 'catalyst_gates': {'PROVENANCE': {
            'allowed': False, 'would_allow': False, 'enforcement_mode': 'enforce'}}}
        with patch('services.runtime.candidate_lifecycle.begin_candidate', return_value=context), \
             patch('services.runtime.candidate_lifecycle.checkpoint'), patch('services.runtime.candidate_lifecycle.reject'), \
             patch.object(agent_runner_main, 'get_company_opportunist_summary', return_value={
                 'company': {'symbol': 'NVDA'}, 'confidence_counts': {'high': 3}}):
            allowed, skipped = agent_runner_main._filter_company_symbols_by_high_confidence_support(
                ['NVDA'], minimum_high_confidence_articles=2, max_age_days=5)
        self.assertEqual(allowed, [])
        self.assertEqual(skipped[0]['skip_reason'], 'provenance_rejected')


if __name__ == '__main__':
    unittest.main()
