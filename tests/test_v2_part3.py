from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from db_common import get_connection
from market_db import initialize_database, record_manager_decision_history, record_option_trade_execution
from news_db import initialize_database as initialize_news, add_company_news_article, add_company_opportunist_impact
from news_db_modules.catalysts import build_catalyst_state, backfill_catalysts, enrich_impact
from market_db_modules.candidate_lifecycle import record_candidate, record_candidate_rejection
from market_db_modules.catalyst_research import record_catalyst_gate_observation, freshness_outcome_report
from services.candidate_lifecycle import GateResult
from services.catalyst_gates import evaluate_catalyst_gates, CatalystGatePolicy
from catalyst_models import classify_catalyst, HALF_LIFE_MINUTES

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

    def test_capture_subtype_and_coarse_family_are_separate(self):
        result = classify_catalyst(TITLE)
        self.assertEqual((result['catalyst_type'], result['catalyst_subtype'], result['catalyst_family']),
                         ('guidance', 'raise_revenue', 'guidance_raise'))
        self.assertEqual(classify_catalyst('Nvidia cuts margin guidance')['catalyst_family'], 'guidance_cut')

    def test_ambiguous_direction_does_not_make_positive_training_label(self):
        result = classify_catalyst('Nvidia earnings beat revenue but miss EPS')
        self.assertEqual((result['direction'], result['catalyst_family']), ('mixed', 'other'))
        self.assertEqual(result['classification_status'], 'needs_review')

    def test_wire_and_reposts_remain_one_root(self):
        self.article(1)
        self.article(2, 'finance.yahoo.com')
        self.article(3, 'investing.com')
        state = self.state()
        self.assertEqual((state['article_count'], state['independent_root_count']), (3, 1))
        with closing(get_connection(self.db)) as conn, conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM root_events WHERE catalyst_id=?', (state['catalyst_id'],)).fetchone()[0], 1)

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

    def test_unknown_primary_url_does_not_manufacture_confirmation(self):
        self.article(1, 'example.com', body='Unattributed commentary', raw_json={
            'provenance': {'primary_source_url': 'https://unverified.example/document/1'}})
        self.assertEqual(self.state()['independent_root_count'], 0)
        self.assertFalse(self.state()['primary_source_present'])

    def test_source_map_distinguishes_court_and_company_release(self):
        self.article(1, 'uscourts.gov', body='Court ruling', title='Nvidia wins court ruling')
        self.article(2, 'investor.nvidia.com', body='Company announcement')
        with closing(get_connection(self.db)) as conn, conn:
            kinds = [row[0] for row in conn.execute('SELECT source_type FROM news_articles ORDER BY id')]
        self.assertEqual(kinds, ['court_document', 'company_release'])

    def test_repost_cannot_refresh_the_root_clock(self):
        self.article(1)
        before = self.state()['latest_root_event_time']
        self.article(2, 'finance.yahoo.com', published_at=NOW.isoformat())
        self.assertEqual(self.state()['latest_root_event_time'], before)
        self.assertEqual(self.state()['article_count'], 2)

    def test_reingestion_cannot_reset_original_root_time(self):
        self.article(1)
        before = self.state()['latest_root_event_time']
        self.article(1, published_at=NOW.isoformat())
        self.assertEqual(self.state()['latest_root_event_time'], before)
        self.assertEqual(self.candidate()['catalysts'][0]['latest_root_event_time'], before)

    def test_new_independent_reporting_refreshes_effective_age(self):
        self.article(1)
        self.article(2, 'cnbc.com', body='Independent reporting based on executive interviews.',
                     published_at=(NOW-timedelta(days=1, hours=2)).isoformat())
        state = self.state()
        self.assertEqual(state['independent_root_count'], 2)
        gate = evaluate_catalyst_gates(state, as_of=NOW)['FRESHNESS']
        self.assertTrue(gate['metrics']['refreshed_by_new_root_event'])
        self.assertLess(gate['metrics']['effective_age_minutes'], gate['metrics']['catalyst_age_minutes'])

    def test_explicit_event_time_is_distinct_from_publication(self):
        when = NOW-timedelta(days=4)
        self.article(1, raw_json={'provenance': {'event_time': when.isoformat()}})
        self.assertEqual(self.state()['first_public_time'], when.isoformat())

    def test_future_evidence_is_excluded_from_candidate_state(self):
        self.article(1)
        second = self.article(2, 'cnbc.com', body='Independent reporting')
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('UPDATE news_articles SET first_seen_time=? WHERE id=?', ((NOW+timedelta(days=1)).isoformat(), second))
        context = self.candidate()
        self.assertEqual(context['catalysts'][0]['independent_root_count'], 1)
        self.assertEqual(self.state()['independent_root_count'], 2)

    def test_every_candidate_has_a_catalyst_even_without_evidence(self):
        context = self.candidate()
        self.assertTrue(context['catalyst_id'].startswith('catalyst:unknown:'))
        self.assertEqual(context['catalyst_family'], 'other')
        self.assertEqual(context['catalysts'][0]['independent_root_count'], 0)

    def test_impact_fields_are_derived_and_invalid_roots_rejected(self):
        article_id = self.article(1)
        impact_id = add_company_opportunist_impact(article_id, 1, raw_json={'reason': 'Forward expectations'}, db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            row = conn.execute('SELECT * FROM company_opportunist_impacts WHERE id=?', (impact_id,)).fetchone()
            self.assertEqual(row['catalyst_family'], 'guidance_raise')
            self.assertTrue(json.loads(row['root_event_ids_json'])[0].startswith('root:'))
            for roots in ([str(article_id)], ['root:invented'], [article_id]):
                with self.assertRaises(ValueError):
                    enrich_impact({'root_event_ids': roots}, article_id, conn=conn)

    def test_candidate_catalyst_stamps_decision_rejection_and_order(self):
        self.article(1)
        context = self.candidate()
        decision_id = record_manager_decision_history({'symbol': 'NVDA', 'candidate_id': context['candidate_id']}, db_path=self.db)
        record_candidate_rejection(context, GateResult(False, 'TEST', 'test', {}, {}), db_path=self.db)
        record_option_trade_execution({'submitted': True, 'candidate_context': context, 'symbol': 'NVDA',
            'selected_option_symbol': 'NVDA261016C00150000', 'order': {'id': 'entry', 'qty': 1, 'side': 'buy'}}, paper=True, db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            for table in ('manager_decision_history', 'candidate_rejections', 'option_trade_executions'):
                row = conn.execute(f'SELECT catalyst_id, catalyst_family FROM {table}').fetchone()
                self.assertEqual(tuple(row), (context['catalyst_id'], 'guidance_raise'))

    def test_backfill_is_repeatable_and_preserves_root_ids(self):
        self.article(1)
        self.article(2, 'cnbc.com', body='Independent interviews')
        before = self.state()
        first = backfill_catalysts(db_path=self.db)
        second = backfill_catalysts(db_path=self.db)
        self.assertEqual(first, second)
        self.assertEqual(self.state()['root_event_ids'], before['root_event_ids'])
        with closing(get_connection(self.db)) as conn, conn:
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_additive_migration_preserves_decisions(self):
        record_manager_decision_history({'symbol': 'NVDA'}, db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('ALTER TABLE manager_decision_history DROP COLUMN catalyst_family')
        initialize_database(db_path=self.db)
        initialize_database(db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM manager_decision_history').fetchone()[0], 1)
            self.assertIn('catalyst_family', {row['name'] for row in conn.execute('PRAGMA table_info(manager_decision_history)')})

    def test_shadow_age_quartiles_include_untraded_candidates_without_fake_pnl(self):
        self.article(1)
        for index in range(8):
            context = self.candidate()
            state = {**context['catalysts'][0], 'first_public_time': (NOW-timedelta(days=index+1)).isoformat(), 'latest_root_event_time': None}
            gate = evaluate_catalyst_gates(state, as_of=NOW)['FRESHNESS']
            record_catalyst_gate_observation(context, gate, db_path=self.db)
            record_catalyst_gate_observation(context, gate, db_path=self.db)
            if index == 0:
                record_manager_decision_history({'symbol': 'NVDA', 'candidate_id': context['candidate_id'],
                    'trade_executed': True, 'realized_pnl_pct': 12, 'closed_at': NOW.isoformat()}, db_path=self.db)
        report = freshness_outcome_report(db_path=self.db)
        self.assertEqual(sum(row['candidates'] for row in report['quartiles']), 8)
        self.assertEqual(sum(row['resolved_candidates'] for row in report['quartiles']), 1)
        self.assertEqual(report['quartiles'][0]['mean_realized_pnl_pct'], 12)
        self.assertIsNone(report['quartiles'][1]['mean_realized_pnl_pct'])


class GateTests(unittest.TestCase):
    def state(self, **extra):
        return {'catalyst_id': 'catalyst:fixture', 'catalyst_family': 'analyst', 'independent_root_count': 1,
                'primary_source_present': False, 'evidence_score': .9, 'first_public_time': (NOW-timedelta(days=4)).isoformat(),
                'latest_root_event_time': None, 'expected_half_life_minutes': 1440, **extra}

    def test_decay_threshold_and_shadow_default(self):
        gate = evaluate_catalyst_gates(self.state(), as_of=NOW)['FRESHNESS']
        self.assertAlmostEqual(gate['metrics']['remaining_information_value'], .0625)
        self.assertFalse(gate['would_allow'])
        self.assertTrue(gate['allowed'])
        self.assertEqual(gate['metrics']['calibration'], 'research_prior')

    def test_boundary_is_inclusive(self):
        gate = evaluate_catalyst_gates(self.state(first_public_time=(NOW-timedelta(days=2)).isoformat()), as_of=NOW)['FRESHNESS']
        self.assertTrue(gate['would_allow'])

    def test_enforcement_is_explicit(self):
        policy = CatalystGatePolicy(provenance_mode='enforce', freshness_mode='enforce')
        gates = evaluate_catalyst_gates(self.state(), as_of=NOW, policy=policy)
        self.assertFalse(gates['PROVENANCE']['allowed'])
        self.assertFalse(gates['FRESHNESS']['allowed'])

    def test_unknown_and_future_times_are_not_fresh(self):
        for value in (None, (NOW+timedelta(days=1)).isoformat()):
            gate = evaluate_catalyst_gates(self.state(first_public_time=value), as_of=NOW)['FRESHNESS']
            self.assertIsNone(gate['metrics']['remaining_information_value'])
            self.assertFalse(gate['would_allow'])

    def test_event_dependent_ma_has_no_invented_decay(self):
        self.assertNotIn('ma', HALF_LIFE_MINUTES)
        gate = evaluate_catalyst_gates(self.state(expected_half_life_minutes=None), as_of=NOW)['FRESHNESS']
        self.assertEqual(gate['reason_codes'], ['unavailable_half_life_prior'])

    def test_primary_exception_requires_score_and_logs_separately(self):
        for score, expected in ((.749, False), (.75, True)):
            gate = evaluate_catalyst_gates(self.state(primary_source_present=True, evidence_score=score), as_of=NOW)['PROVENANCE']
            self.assertEqual(gate['would_allow'], expected)
            self.assertEqual(gate['metrics']['single_primary_exception'], expected)

    def test_bad_mode_is_not_silently_enforced(self):
        with patch.dict('os.environ', {'V2_FRESHNESS_MODE': 'typo'}), self.assertRaises(ValueError):
            CatalystGatePolicy.from_env()

    def test_enforcement_cannot_bypass_missing_candidate_state(self):
        from services.catalyst_gates import catalyst_admission_allowed
        self.assertTrue(catalyst_admission_allowed(None, policy=CatalystGatePolicy()))
        self.assertFalse(catalyst_admission_allowed(None, policy=CatalystGatePolicy(freshness_mode='enforce')))
        self.assertFalse(catalyst_admission_allowed({'catalyst_gates': {}}, policy=CatalystGatePolicy(provenance_mode='enforce')))

    def test_continuation_primary_exception_and_separate_catalyst_counts(self):
        from services.evidence_continuation_gate import _evidence_snapshot, EvidenceContinuationPolicy
        payload = {'grounding': {'article_ids': [1, 2]}, 'supporting_articles': {'article_summaries': [
            {'article_id': 1, 'evidence_score': .8, 'source_type': 'primary_filing', 'primary_ticker': 'NVDA',
             'root_source_id': 'url:filing', 'root_event_id': 'root:a', 'catalyst_id': 'catalyst:a'},
            {'article_id': 2, 'evidence_score': .8, 'source_type': 'original_reporting', 'primary_ticker': 'NVDA',
             'root_source_id': 'publisher:cnbc', 'root_event_id': 'root:b', 'catalyst_id': 'catalyst:b'}]}}
        result = _evidence_snapshot(payload, EvidenceContinuationPolicy())
        self.assertEqual(result['independent_source_count'], 1)
        self.assertTrue(result['primary_source_exception'])

    def test_primary_source_exception_replaces_legacy_article_count_only_when_enforced(self):
        from tests.test_agent_runner_main import agent_runner_main
        for mode, expected in (('shadow', []), ('enforce', ['NVDA'])):
            context = {'candidate_id': 'fixture', 'catalyst_gates': {'PROVENANCE': {
                'allowed': True, 'would_allow': True, 'enforcement_mode': mode,
                'reason_codes': ['pass_with_single_primary_root']}}}
            with patch('services.candidate_lifecycle.begin_candidate', return_value=context), \
                 patch('services.candidate_lifecycle.checkpoint'), patch('services.candidate_lifecycle.reject'), \
                 patch.object(agent_runner_main, 'get_company_opportunist_summary', return_value={
                     'company': {'symbol': 'NVDA'}, 'confidence_counts': {'high': 1}}):
                allowed, skipped = agent_runner_main._filter_company_symbols_by_high_confidence_support(
                    ['NVDA'], minimum_high_confidence_articles=2, max_age_days=5)
            self.assertEqual(allowed, expected)

    def test_provenance_rejection_blocks_even_when_legacy_article_count_passes(self):
        from tests.test_agent_runner_main import agent_runner_main
        context = {'candidate_id': 'fixture', 'catalyst_gates': {'PROVENANCE': {
            'allowed': False, 'would_allow': False, 'enforcement_mode': 'enforce'}}}
        with patch('services.candidate_lifecycle.begin_candidate', return_value=context), \
             patch('services.candidate_lifecycle.checkpoint'), patch('services.candidate_lifecycle.reject'), \
             patch.object(agent_runner_main, 'get_company_opportunist_summary', return_value={
                 'company': {'symbol': 'NVDA'}, 'confidence_counts': {'high': 3}}):
            allowed, skipped = agent_runner_main._filter_company_symbols_by_high_confidence_support(
                ['NVDA'], minimum_high_confidence_articles=2, max_age_days=5)
        self.assertEqual(allowed, [])
        self.assertEqual(skipped[0]['skip_reason'], 'provenance_rejected')

    def test_fresh_repost_does_not_override_old_root_age(self):
        gate = evaluate_catalyst_gates(self.state(latest_evidence_time=NOW.isoformat()), as_of=NOW)['FRESHNESS']
        self.assertFalse(gate['would_allow'])


if __name__ == '__main__':
    unittest.main()
