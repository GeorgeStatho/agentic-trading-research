from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import logging
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database
from market_db_modules.research_runs import start_run, collect_run
from market_db_modules.research_reports import monthly_report, promote_gate, promote_champion, calibration_table, strategy_table
from services.contract_policy import ContractPolicy
from services.research_contract import ResearchPolicy, assert_information_cutoff, canonical, fingerprint, strategy_decisions
from services.research_inputs import load_inputs, baseline_contract
from services.research_promotion import approved, guard_policy, champion_allows, source_hash
from services.shadow_portfolio import simulate_option, evaluate_gate
from services.order_candidates import OrderCandidateBuilder
from services.trade_executor import TradeExecutionSession, OptionTradeExecutor

START = '2026-09-01T20:00:00+00:00'
ENTRY = '2026-09-02T20:00:00+00:00'
END = '2026-09-10T20:00:00+00:00'
POLICY = ResearchPolicy()


def contract(symbol='NVDA261016C00101000'):
    return {'symbol': symbol, 'contract_type': 'call', 'expiration_date': '2026-10-16',
            'greeks': {'delta': .5}, 'latest_quote': {'bid_price': 1.9, 'ask_price': 2, 'timestamp': ENTRY}}


def quote(day, bid=2.1, ask=2.2, provider='fixture'):
    return {'id': day, 'quote_at': f'2026-09-{day:02d}T20:00:00+00:00',
            'bid_price': bid, 'ask_price': ask, 'provider': provider}


def quotes(bid=2.1):
    return [quote(2, 1.9, 2), quote(3, bid, max(2.2, bid)), quote(4, bid, max(2.2, bid)), quote(7, bid, max(2.2, bid))]


class ContractTests(unittest.TestCase):
    def test_cutoff_compares_timezones(self):
        self.assertEqual(assert_information_cutoff(['2026-09-02T16:00:00-04:00'], ENTRY), ENTRY)

    def test_missing_and_future_timestamps_are_rejected(self):
        for value in (None, END):
            with self.subTest(value=value), self.assertRaises(ValueError):
                assert_information_cutoff([value], ENTRY)

    def test_selector_clock_is_scoped_to_original_date(self):
        from agent_helpers.deterministic_option_selector.market import _get_dte, selection_date
        before = _get_dte(contract())
        with selection_date(datetime(2026, 10, 1, tzinfo=timezone.utc)):
            self.assertEqual(_get_dte(contract()), 15)
        self.assertEqual(_get_dte(contract()), before)

    def test_invalid_policy_cannot_lower_promotion_floor(self):
        for fields in ({'minimum_promotion_samples': 99}, {'holding_days': 0}, {'fee_per_contract_side': float('nan')}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                ResearchPolicy(**fields)

    def test_missing_baseline_inputs_are_not_cash_or_future_inferred(self):
        self.assertTrue(all(value is None for value in strategy_decisions({}, POLICY).values()))

    def test_baselines_and_cash_are_deterministic(self):
        decisions = strategy_decisions({'sentiment': 'positive', 'relative_volume': 1, 'trend': 'bullish',
            'historical_expectancy': -.03, 'catalyst_direction': 'negative', 'strategist_direction': 'put',
            'manager_direction': 'put', 'gates': {'REGIME': False}, 'production_allowed': False}, POLICY)
        self.assertEqual(list(decisions.values()), ['skip', 'call', 'put', 'put', 'put', 'skip'])

    def test_ordinal_components_are_preserved_and_legacy_missing_stays_unknown(self):
        from agent_stages.manager_normalization import normalize_recommendation
        result = normalize_recommendation({'decision': 'call', 'confidence': 'high', 'reason': 'fixture',
                                           'thesis_confidence': 'medium', 'timing_confidence': .8})
        self.assertEqual((result['thesis_confidence'], result['timing_confidence'], result['execution_confidence']), ('medium', None, None))

    def test_calibration_detects_nonmonotonic_accuracy(self):
        rows = [{'features': {'model': 'm', 'catalyst_family': 'earnings', 'regime': 'trend_up',
                              'manager_direction': 'call', 'confidence': {'thesis_confidence': level}},
                 'residual_return': move} for level, move in (('low', .1), ('medium', -.1), ('high', .2))]
        table = calibration_table(rows)
        thesis = next(row for row in table if row['component'] == 'thesis_confidence')
        self.assertFalse(thesis['monotonic'])

    def test_missing_calibration_labels_do_not_become_losses(self):
        table = calibration_table([{'features': {'confidence': {'thesis_confidence': 'high'}, 'manager_direction': 'call'}, 'residual_return': None}])
        self.assertTrue(all(row['monotonic'] is None for row in table))


class ShadowTests(unittest.TestCase):
    def simulate(self, history=None, **kwargs):
        return simulate_option(contract(), history if history is not None else quotes(), cutoff=ENTRY, as_of=END, policy=POLICY, **kwargs)

    def test_spread_fees_horizon_and_dollar_days(self):
        result = self.simulate()
        self.assertAlmostEqual(result['hypothetical_pnl'], 8.7)
        self.assertAlmostEqual(result['deployed_dollar_days'], 200.65 * 5)
        self.assertEqual(result['hypothetical_exit']['reason'], 'catalyst_expired')

    def test_stop_precedes_time_exit_and_lists_coactive_reasons(self):
        result = self.simulate([quote(2, 1.9, 2), quote(3), quote(4), quote(7, 1.2, 1.3)])
        self.assertEqual(result['marks'][-1]['coactive_exit_reasons'], ['risk_stop', 'catalyst_expired', 'trailing_stop'])

    def test_take_profit_and_trailing_exit(self):
        self.assertEqual(self.simulate([quote(2, 1.9, 2), quote(3, 3.2, 3.3)])['hypothetical_exit']['reason'], 'take_profit')
        result = self.simulate([quote(2, 1.9, 2), quote(3, 2.9, 3), quote(4, 2.2, 2.3)])
        self.assertEqual(result['hypothetical_exit']['reason'], 'trailing_stop')

    def test_zero_bid_can_realize_full_premium_loss(self):
        self.assertAlmostEqual(self.simulate(quotes(0))['hypothetical_pnl'], -201.3)

    def test_missing_entry_quote_is_not_a_free_entry(self):
        self.assertEqual(self.simulate(quotes()[1:])['status'], 'missing_entry_quote')

    def test_future_quote_cannot_be_entry(self):
        self.assertEqual(self.simulate([quote(3)])['status'], 'missing_entry_quote')

    def test_crossed_or_nonfinite_quotes_are_excluded(self):
        self.assertEqual(self.simulate([quote(2, 3, 2), quote(3, float('nan'))])['status'], 'missing_entry_quote')

    def test_provider_cannot_change_mid_trade(self):
        history = [quote(2, 1.9, 2), quote(3, 1, 2, 'other')]
        self.assertEqual(self.simulate(history)['status'], 'missing_exit_quote')

    def test_sparse_marks_cannot_hide_stop_breaches(self):
        self.assertEqual(self.simulate([quote(2, 1.9, 2), quote(7)])['status'], 'missing_daily_marks')

    def test_observed_market_session_cannot_be_skipped_inside_gap_limit(self):
        result = simulate_option(contract(), [quote(2, 1.9, 2), quote(4)], cutoff=ENTRY, as_of=END,
                                 policy=POLICY, market_sessions=[quote(3)['quote_at'], quote(4)['quote_at']])
        self.assertEqual(result['status'], 'missing_daily_marks')

    def test_outcome_quotes_after_report_cutoff_are_excluded(self):
        result = simulate_option(contract(), quotes(), cutoff=ENTRY, as_of='2026-09-03T20:00:00Z', policy=POLICY)
        self.assertEqual(result['status'], 'pending')

    def test_open_session_cannot_finalize_a_daily_outcome(self):
        # Collecting at 13:00 New York sees a spike the rest of the session can still erase.
        midday = {'id': 'midday', 'quote_at': '2026-09-03T16:00:00+00:00', 'bid_price': 3.2,
                  'ask_price': 3.3, 'provider': 'fixture'}
        history = [quote(2, 1.9, 2), midday]
        during = simulate_option(contract(), history, cutoff=ENTRY, as_of='2026-09-03T17:00:00+00:00', policy=POLICY)
        self.assertEqual((during['status'], during['hypothetical_exit']), ('pending', None))
        # Once the session has closed that same quote is the day's best available mark.
        self.assertEqual(self.simulate(history)['hypothetical_exit']['reason'], 'take_profit')

    def test_closing_quote_replaces_an_earlier_mark_from_the_same_session(self):
        midday = {'id': 'midday', 'quote_at': '2026-09-03T16:00:00+00:00', 'bid_price': 3.2,
                  'ask_price': 3.3, 'provider': 'fixture'}
        result = self.simulate([quote(2, 1.9, 2), midday, quote(3, 2.1, 2.2)])
        self.assertIsNone(result['hypothetical_exit'])

    def test_unobserved_session_cannot_finalize_an_exit(self):
        result = simulate_option(contract(), quotes(), cutoff=ENTRY, as_of=END, policy=POLICY,
                                 market_sessions=[quote(3)['quote_at'], quote(4)['quote_at']])
        self.assertEqual(result['status'], 'pending')

    def test_a_day_session_coverage_has_passed_is_unevaluable_not_forever_pending(self):
        # Sessions run past the 09-07 quote without ever containing it, so waiting for its
        # evidence would re-collect a day that can never be confirmed.
        sessions = [quote(day)['quote_at'] for day in (3, 4, 8)]
        result = simulate_option(contract(), quotes(), cutoff=ENTRY, as_of=END, policy=POLICY, market_sessions=sessions)
        self.assertEqual(result['status'], 'missing_daily_marks')

    def test_session_evidence_is_matched_on_the_new_york_date(self):
        # 00:30Z belongs to the previous New York session, which is the basis marks use.
        result = simulate_option(contract(), [quote(2, 1.9, 2), quote(3, 3.2, 3.3)], cutoff=ENTRY, as_of=END,
                                 policy=POLICY, market_sessions=['2026-09-04T00:30:00+00:00'])
        self.assertEqual(result['hypothetical_exit']['reason'], 'take_profit')
        self.assertTrue(result['verified_daily_marks'])

    def test_expiry_shorter_than_hold_does_not_fabricate_settlement(self):
        result = simulate_option({**contract(), 'expiration_date': '2026-09-04'}, quotes(), cutoff=ENTRY, as_of=END, policy=POLICY)
        self.assertEqual(result['status'], 'expiry_before_horizon')

    def test_gate_confusion_matrix_and_all_denominators(self):
        rows = [{'gates': {'G': passed}, 'result': {'status': 'complete', 'hypothetical_pnl': pnl, 'deployed_dollar_days': 100}}
                for passed, pnl in ((True, 20), (False, -30), (False, 10))]
        gate = evaluate_gate(rows, 'G')
        self.assertEqual((gate['gate_value'], gate['rejected_winners'], gate['rejected_losers'], gate['gate_efficiency']), (20, 1, 1, 30))
        self.assertEqual(gate['expectancy'], {'per_executed_trade': 20, 'per_candidate_observed': 20 / 3, 'per_deployed_dollar_day': .2})

    def test_unseen_is_not_passed_and_missing_is_not_zero_loss(self):
        gate = evaluate_gate([{'gates': {}, 'result': {'status': 'complete'}},
                              {'gates': {'G': False}, 'result': {'status': 'missing_entry_quote'}}], 'G')
        self.assertEqual((gate['n_seen'], gate['n_passed'], gate['n_missing_outcomes'], gate['coverage_complete']), (1, 0, 1, False))


class ExecutionIdentityTests(unittest.TestCase):
    """A research identity must reach the executor's guard, not stop at a conversion."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'research.db'
        initialize_database(db_path=self.db)

    @staticmethod
    def manager_result(**extra):
        return {'candidate_id': 'candidate-000', 'candidate_context': {},
                'company': {'company_id': 1, 'symbol': 'NVDA', 'name': 'NVIDIA', 'sector_key': 'technology'},
                'recommendation': {'decision': 'call', 'confidence': 'high', 'reason': 'fixture'},
                'strategist_recommendation': {'decision': 'trade_candidate'},
                'entry_gate': {'allowed': True}, 'market_regime_gate': {'allowed': True},
                'selected_option': {'symbol': 'NVDA-option'}, **extra}

    def test_research_identity_survives_conversion_and_blocks_execution(self):
        built = OrderCandidateBuilder().build_from_manager_result(
            self.manager_result(research_run_id='challenger', research_strategy='full_v2'))
        self.assertEqual((built['research_run_id'], built['research_strategy']), ('challenger', 'full_v2'))

        seen = {}
        def spy(candidate, **kwargs):
            seen['candidate'] = candidate
            return champion_allows(candidate, db_path=self.db)

        executor = OptionTradeExecutor(settings=SimpleNamespace(), trading_gateway=Mock(),
                                       logger=logging.getLogger('fixture'))
        session = TradeExecutionSession(available_buying_power=20000, max_deployable_buying_power=10000,
                                        remaining_deployable_buying_power=10000, base_order_qty=1)
        with patch('services.research_promotion.champion_allows', spy):
            result = executor.execute_candidate(trading_client=Mock(), candidate=built, session=session)
        self.assertEqual(result, {'submitted': False, 'portfolio_guardrail': 'research_champion_only'})
        self.assertEqual(seen['candidate']['research_run_id'], 'challenger')
        executor._trading_gateway.submit_option_market_order.assert_not_called()

    def test_untagged_candidate_is_not_blocked_by_the_research_guard(self):
        built = OrderCandidateBuilder().build_from_manager_result(self.manager_result())
        self.assertIsNone(built['research_run_id'])
        self.assertTrue(champion_allows(built, db_path=self.db))


class ResearchDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'research.db'
        initialize_database(db_path=self.db)
        self.manifest = {'pipeline_version': 'fixture-v1', 'gate_config': {'contracts': asdict(ContractPolicy())}}

    def run_fixture(self, n=1, kind='frozen_forward'):
        return start_run(self.manifest, kind=kind, target_candidates=n, now=START, db_path=self.db)['run_id']

    def candidate(self, n=0, passed=True, selected=True, version=None, entry=ENTRY, decision='call'):
        symbol = f'NVDA-option-{n}'
        selected_contract = contract(symbol)
        selected_contract['latest_quote']['timestamp'] = entry
        candidate_id = f'candidate-{n:03d}'
        manifest = version or self.manifest
        way = 'positive' if decision == 'call' else 'negative'
        catalyst = {'catalyst_family': 'earnings', 'direction': way, 'independent_root_count': 2, 'evidence_score': .8}
        payload = {'recommendation': {'decision': decision, 'confidence': 'high', 'thesis_confidence': 'high',
                    'timing_confidence': 'medium', 'execution_confidence': 'low'}, 'manager_model': 'fixture-model',
                   'strategist_recommendation': {'decision': decision},
                   'candidate_context': {'catalysts': [catalyst]}, 'selected_option': selected_contract if selected else None,
                   'market_context': {'market_regime': {'state': 'trend_up' if n % 2 else 'range'},
                       'option_market': {'contracts': [selected_contract]},
                       'underlying_price_history': {'technical_summary': {'trend_state': 'bullish'},
                            'technical_indicators': {'relative_volume': {'relative_volume_20d': 2}}},
                       '_v2_scenario_distribution': {'returns': [.02] * 30, 'minimum_samples': 30, 'as_of': entry}}}
        original = {'recommendation': payload['recommendation'], 'payload': {'opportunist_rollup': {'company': {'dominant_direction': way}}}}
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('''INSERT INTO candidates(candidate_id,symbol,created_at,catalyst_ids_json,article_ids_json,pipeline_version_json)
                VALUES (?,?,?,?,?,?)''', (candidate_id, 'NVDA', entry, '[]', '[]', canonical(manifest)))
            observations = [('pre_gates', original), ('post_contract_selection', payload),
                            ('LIQUIDITY', [{'option_symbol': symbol, 'would_allow': passed}])]
            observations.append(('candidate_complete', {}))
            for stage, value in observations:
                envelope = {'payload': value, 'information_cutoff_time': entry}
                serialized = canonical(envelope)
                from hashlib import sha256
                conn.execute('''INSERT INTO candidate_snapshots
                    (candidate_id,stage,captured_at,information_cutoff_time,pipeline_version,snapshot_version,snapshot_hash,snapshot_json)
                    VALUES (?,?,?,?,?,?,?,?)''', (candidate_id, stage, entry, entry, manifest['pipeline_version'], 'v1', sha256(serialized.encode()).hexdigest(), serialized))
            if not passed:
                conn.execute('''INSERT INTO candidate_rejections(candidate_id,stage,reason_code,occurred_at,shadow)
                    VALUES (?,'LIQUIDITY','spread',?,1)''', (candidate_id, entry))
            for item in quotes(2.1 if passed else 1.2):
                conn.execute('''INSERT INTO historical_option_quotes
                    (option_symbol,quote_at,bid_price,ask_price,provider,created_at) VALUES (?,?,?,?,?,?)''',
                    (symbol, item['quote_at'], item['bid_price'], item['ask_price'], 'fixture', item['quote_at']))
        return candidate_id

    def benchmarks(self):
        with closing(get_connection(self.db)) as conn, conn:
            for symbol in ('SMH', 'QQQ', 'SPY'):
                for day in (2, 3, 4, 7):
                    at = quote(day)['quote_at']
                    conn.execute('''INSERT INTO historical_underlying_bars
                        (symbol,bar_at,timeframe,close_price,provider,raw_json,created_at) VALUES (?,?,'1d',100,'fixture',?,?)''',
                        (symbol, at, canonical({'available_at': at}), at))

    def collect_report(self, run_id):
        collect_run(run_id, as_of=END, db_path=self.db)
        return monthly_report(run_id, '2026-09', as_of=END, db_path=self.db)

    def test_additive_migration_is_idempotent(self):
        self.candidate()
        initialize_database(db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertFalse(conn.execute('PRAGMA foreign_key_check').fetchall())

    def test_every_strategy_persisted_and_repeat_collection_is_idempotent(self):
        run_id = self.run_fixture()
        self.candidate()
        report = self.collect_report(run_id)
        self.assertEqual(self.collect_report(run_id), report)
        with closing(get_connection(self.db)) as conn, conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM research_shadow_positions').fetchone()[0], 9)

    def test_report_is_cited_and_never_claims_missing_benchmarks_are_flat(self):
        run_id = self.run_fixture()
        self.candidate()
        report = self.collect_report(run_id)
        self.assertEqual(report['claim_source']['run_id'], run_id)
        self.assertTrue(all(row['rank'] is None for row in report['strategies']))

    def test_benchmarks_use_same_capital_and_costs(self):
        run_id = self.run_fixture()
        self.candidate()
        self.benchmarks()
        report = self.collect_report(run_id)
        self.assertTrue(all(row['paired_n'] == 1 for row in report['strategies']))
        self.assertLess(next(row for row in report['strategies'] if row['strategy'] == 'QQQ')['net_pnl'], 0)

    def test_benchmark_cannot_enter_at_a_close_that_already_happened(self):
        from market_db_modules.research_runs import benchmark_position
        reference = {'status': 'complete', 'capital': 200,
                     'hypothetical_entry': {'at': '2026-09-02T17:00:00+00:00'},
                     'hypothetical_exit': {'at': '2026-09-07T20:00:00+00:00'}}
        with closing(get_connection(self.db)) as conn, conn:
            for at, close in (('2026-09-01T20:00:00+00:00', 100), ('2026-09-02T20:00:00+00:00', 120),
                              ('2026-09-07T20:00:00+00:00', 120)):
                conn.execute("""INSERT INTO historical_underlying_bars
                    (symbol,bar_at,timeframe,close_price,provider,raw_json,created_at)
                    VALUES ('QQQ',?,'1d',?,'fixture',?,?)""", (at, close, canonical({'available_at': at}), at))
            result = benchmark_position(conn, 'QQQ', reference, {'information_cutoff_time': ENTRY},
                                        {'policy': POLICY}, END)
        # Yesterday's $100 close was no longer executable at a 13:00 entry.
        self.assertEqual(result['hypothetical_entry']['price'], 120)
        self.assertEqual(result['hypothetical_entry']['at'], '2026-09-02T20:00:00+00:00')
        self.assertLess(result['hypothetical_pnl'], 0)

    def test_window_closing_before_the_first_executable_close_holds_nothing(self):
        from market_db_modules.research_runs import benchmark_position
        # The option round-tripped the same session, so the benchmark never took a position.
        reference = {'status': 'complete', 'capital': 200,
                     'hypothetical_entry': {'at': '2026-09-02T17:00:00+00:00'},
                     'hypothetical_exit': {'at': '2026-09-02T19:00:00+00:00'}}
        with closing(get_connection(self.db)) as conn, conn:
            for at, close in (('2026-09-02T20:00:00+00:00', 120), ('2026-09-03T20:00:00+00:00', 130)):
                conn.execute("""INSERT INTO historical_underlying_bars
                    (symbol,bar_at,timeframe,close_price,provider,raw_json,created_at)
                    VALUES ('SMH',?,'1d',?,'fixture',?,?)""", (at, close, canonical({'available_at': at}), at))
            result = benchmark_position(conn, 'SMH', reference, {'information_cutoff_time': ENTRY},
                                        {'policy': POLICY}, END)
        # A round-trip cost here would be charged for a position that was never held.
        self.assertEqual((result['status'], result['held']), ('complete', False))
        self.assertEqual((result['hypothetical_pnl'], result['deployed_dollar_days']), (0, 0))

    def test_benchmark_provider_must_cover_both_ends_of_the_window(self):
        from market_db_modules.research_runs import benchmark_position
        reference = {'status': 'complete', 'capital': 200,
                     'hypothetical_entry': {'at': '2026-09-02T17:00:00+00:00'},
                     'hypothetical_exit': {'at': '2026-09-03T20:00:00+00:00'}}
        with closing(get_connection(self.db)) as conn, conn:
            # 'alpaca' sorts first but abandoned its series after the entry bar.
            for provider, at, close in (('alpaca', '2026-09-02T20:00:00+00:00', 120),
                                        ('yfinance', '2026-09-02T20:00:00+00:00', 120),
                                        ('yfinance', '2026-09-03T20:00:00+00:00', 132)):
                conn.execute("""INSERT INTO historical_underlying_bars
                    (symbol,bar_at,timeframe,close_price,provider,raw_json,created_at)
                    VALUES ('QQQ',?,'1d',?,?,?,?)""", (at, close, provider, canonical({'available_at': at}), at))
            result = benchmark_position(conn, 'QQQ', reference, {'information_cutoff_time': ENTRY},
                                        {'policy': POLICY}, END)
        self.assertEqual(result['status'], 'complete')
        self.assertGreater(result['hypothetical_pnl'], 0)

    def test_pre_split_forward_returns_recover_their_raw_residual(self):
        from market_db_modules.pricing_research import backfill_raw_residuals
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("""INSERT INTO candidate_price_observations
                (candidate_id,catalyst_id,observed_at,bucket,snapshot_json,forward_residual_return,forward_at,forward_checked_at)
                VALUES ('legacy','fixture',?,'low',?,.05,?,?)""",
                (ENTRY, canonical({'direction': 'negative'}), ENTRY, ENTRY))
            backfill_raw_residuals(conn)
            row = conn.execute("SELECT * FROM candidate_price_observations WHERE candidate_id='legacy'").fetchone()
        # The stored value folds in the frozen direction, so re-applying it is exact.
        self.assertAlmostEqual(row['forward_raw_residual_return'], -.05)

    def test_correct_bearish_direction_is_not_scored_as_a_miss(self):
        run_id = self.run_fixture()
        candidate_id = self.candidate(decision='put')
        with closing(get_connection(self.db)) as conn, conn:
            # A bearish catalyst whose stock fell 5%: raw residual negative, adjusted positive.
            conn.execute("""INSERT INTO candidate_price_observations
                (candidate_id,catalyst_id,observed_at,bucket,snapshot_json,forward_residual_return,
                 forward_raw_residual_return,forward_at,forward_checked_at)
                VALUES (?,'fixture',?,'low','{}',.05,-.05,?,?)""", (candidate_id, ENTRY, ENTRY, ENTRY))
        report = self.collect_report(run_id)
        thesis = next(row for row in report['calibration'] if row['component'] == 'thesis_confidence')
        high = next(item for item in thesis['bins'] if item['bin'] == 'high')
        self.assertEqual((high['n_resolved'], high['direction_accuracy'], high['mean_residual_return']), (1, 1, -.05))

    def test_forward_excludes_candidates_before_freeze_and_manifest_drift(self):
        run_id = self.run_fixture(3)
        self.candidate(0, entry='2026-08-30T20:00:00+00:00')
        self.candidate(1, version={'pipeline_version': 'changed'})
        self.candidate(2)
        result = collect_run(run_id, as_of=END, db_path=self.db)
        self.assertEqual((result['candidates'], result['skipped_manifest_mismatches']), (1, 1))

    def test_frozen_run_stops_at_n_candidates(self):
        run_id = self.run_fixture()
        self.candidate(0)
        self.candidate(1)
        self.assertEqual(collect_run(run_id, as_of=END, db_path=self.db)['candidates'], 1)

    def test_changed_evaluator_source_cannot_continue_experiment(self):
        run_id = self.run_fixture()
        with patch('services.research_versions.evaluator_source_hash', return_value='changed'), self.assertRaises(ValueError):
            collect_run(run_id, as_of=END, db_path=self.db)

    def test_changed_frozen_configuration_is_rejected(self):
        run_id = self.run_fixture()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE research_runs SET policy_json='{}' WHERE run_id=?", (run_id,))
        # {} is the default policy, so change the actual canonical manifest.
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE research_runs SET manifest_json=? WHERE run_id=?", (canonical({'pipeline_version': 'tampered'}), run_id))
        with self.assertRaises(ValueError):
            collect_run(run_id, as_of=END, db_path=self.db)

    def test_changed_snapshot_hash_is_rejected(self):
        run_id = self.run_fixture()
        self.candidate()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE candidate_snapshots SET snapshot_json='{}'")
        with self.assertRaises(ValueError):
            collect_run(run_id, as_of=END, db_path=self.db)

    def test_late_available_quote_cannot_be_used_for_entry(self):
        run_id = self.run_fixture()
        self.candidate()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('UPDATE historical_option_quotes SET created_at=? WHERE quote_at=?', (END, ENTRY))
        report = self.collect_report(run_id)
        self.assertEqual(report['gates'][0]['n_missing_outcomes'], 1)

    def test_completed_shadow_outcome_cannot_be_rewritten_by_quote_backfill(self):
        run_id = self.run_fixture()
        self.candidate()
        before = self.collect_report(run_id)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('UPDATE historical_option_quotes SET bid_price=0 WHERE quote_at>?', (ENTRY,))
        self.assertEqual(self.collect_report(run_id), before)

    def test_future_residual_label_is_not_calibration_evidence(self):
        run_id = self.run_fixture()
        candidate_id = self.candidate()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('''INSERT INTO candidate_price_observations
                (candidate_id,catalyst_id,observed_at,bucket,snapshot_json,forward_residual_return,
                 forward_raw_residual_return,forward_at,forward_checked_at)
                VALUES (?, 'fixture', ?, 'low', '{}', .2, .2, '2026-10-01', '2026-10-01')''', (candidate_id, ENTRY))
        report = self.collect_report(run_id)
        self.assertTrue(all(row['monotonic'] is None for row in report['calibration']))

    def test_small_sample_cannot_promote(self):
        run_id = self.run_fixture()
        self.candidate(passed=False)
        report = self.collect_report(run_id)
        with self.assertRaises(ValueError):
            promote_gate(report['report_id'], 'LIQUIDITY', db_path=self.db)

    def test_future_feature_timestamp_is_rejected_despite_valid_snapshot_hash(self):
        run_id = self.run_fixture()
        self.candidate()
        with closing(get_connection(self.db)) as conn, conn:
            row = conn.execute("SELECT id,snapshot_json FROM candidate_snapshots WHERE stage='post_contract_selection'").fetchone()
            envelope = json.loads(row['snapshot_json'])
            envelope['payload']['market_context']['timestamp'] = END
            serialized = canonical(envelope)
            from hashlib import sha256
            conn.execute('UPDATE candidate_snapshots SET snapshot_json=?,snapshot_hash=? WHERE id=?',
                         (serialized, sha256(serialized.encode()).hexdigest(), row['id']))
        with self.assertRaises(ValueError):
            collect_run(run_id, as_of=END, db_path=self.db)

    def test_freeze_includes_target_observation_count(self):
        run_id = self.run_fixture(200)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('UPDATE research_runs SET target_candidates=100 WHERE run_id=?', (run_id,))
        with self.assertRaises(ValueError):
            collect_run(run_id, as_of=END, db_path=self.db)

    def test_unselected_illiquid_contract_does_not_reject_selected_candidate(self):
        run_id = self.run_fixture()
        self.candidate()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('''INSERT INTO candidate_rejections(candidate_id,stage,reason_code,occurred_at,shadow)
                VALUES ('candidate-000','LIQUIDITY','other_contract_is_wide',?,1)''', (ENTRY,))
        report = self.collect_report(run_id)
        gate = next(item for item in report['gates'] if item['stage'] == 'LIQUIDITY')
        self.assertEqual(gate['n_rejected'], 0)

    def test_possible_liquidity_reselection_does_not_earn_saved_loss_credit(self):
        run_id = self.run_fixture()
        self.candidate(passed=False)
        with closing(get_connection(self.db)) as conn, conn:
            row = conn.execute("SELECT id,snapshot_json FROM candidate_snapshots WHERE stage='LIQUIDITY'").fetchone()
            envelope = json.loads(row['snapshot_json'])
            envelope['payload'].append({'option_symbol': 'alternative', 'would_allow': True})
            serialized = canonical(envelope)
            from hashlib import sha256
            conn.execute('UPDATE candidate_snapshots SET snapshot_json=?,snapshot_hash=? WHERE id=?',
                         (serialized, sha256(serialized.encode()).hexdigest(), row['id']))
        gate = self.collect_report(run_id)['gates'][0]
        self.assertEqual((gate['n_unknown_verdicts'], gate['gate_value'], gate['coverage_complete']), (1, 0, False))

    def test_missing_snapshots_stay_in_cohort(self):
        run_id = self.run_fixture()
        self.candidate()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('DELETE FROM candidate_snapshots')
        report = self.collect_report(run_id)
        self.assertEqual(report['n_observed'], 1)
        self.assertTrue(all(row['n_missing'] == 1 for row in report['strategies']))

    def test_unfinished_candidate_waits_for_terminal_marker(self):
        run_id = self.run_fixture()
        self.candidate()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("DELETE FROM candidate_snapshots WHERE stage='candidate_complete'")
        result = collect_run(run_id, as_of='2026-09-02T20:10:00Z', db_path=self.db)
        self.assertEqual(result['candidates'], 0)

    def test_missing_snapshot_does_not_erase_rejection_reason(self):
        run_id = self.run_fixture()
        self.candidate(passed=False)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('DELETE FROM candidate_snapshots')
        report = self.collect_report(run_id)
        self.assertEqual(report['gates'][0]['n_missing_outcomes'], 1)

    def test_all_months_report_supports_multi_month_experiments(self):
        run_id = self.run_fixture()
        self.candidate()
        collect_run(run_id, as_of=END, db_path=self.db)
        report = monthly_report(run_id, 'all', as_of=END, db_path=self.db)
        self.assertEqual(report['n_observed'], 1)

    def test_gate_and_champion_promotion_require_real_persisted_evidence(self):
        run_id = self.run_fixture(100)
        for n in range(100):
            self.candidate(n, passed=n < 50)
        self.benchmarks()
        report = self.collect_report(run_id)
        proof = promote_gate(report['report_id'], 'LIQUIDITY', db_path=self.db)
        self.assertEqual(proof['sample_size'], 100)
        self.assertTrue(approved('LIQUIDITY', ContractPolicy(liquidity_mode='enforce'), db_path=self.db))
        self.assertFalse(approved('LIQUIDITY', ContractPolicy(maximum_spread_fraction=.07), db_path=self.db))
        # A useful gate does not prove the unmodified production strategy beats baselines.
        with self.assertRaises(ValueError):
            promote_champion(report['report_id'], db_path=self.db)

    def test_historical_replay_is_weaker_and_cannot_promote(self):
        run_id = self.run_fixture(kind='leakage_checked_backtest')
        self.candidate()
        report = self.collect_report(run_id)
        self.assertIn('parametric', report['evidence_warning'])
        with self.assertRaises(ValueError):
            promote_gate(report['report_id'], 'LIQUIDITY', db_path=self.db)

    def test_qualified_champion_is_singleton_and_rejects_other_manifests(self):
        run_id = self.run_fixture(100)
        for n in range(100):
            candidate_id = self.candidate(n)
            bad = contract(f'baseline-{n}')
            with closing(get_connection(self.db)) as conn, conn:
                row = conn.execute("SELECT id,snapshot_json FROM candidate_snapshots WHERE candidate_id=? AND stage='post_contract_selection'",
                                   (candidate_id,)).fetchone()
                envelope = json.loads(row['snapshot_json'])
                envelope['payload']['selected_option']['greeks']['delta'] = .7
                pool = envelope['payload']['market_context']['option_market']['contracts']
                pool[0]['greeks']['delta'] = .7
                pool.append(bad)
                serialized = canonical(envelope)
                from hashlib import sha256
                conn.execute('UPDATE candidate_snapshots SET snapshot_json=?,snapshot_hash=? WHERE id=?',
                             (serialized, sha256(serialized.encode()).hexdigest(), row['id']))
                for item in quotes(1.2):
                    conn.execute('''INSERT INTO historical_option_quotes
                        (option_symbol,quote_at,bid_price,ask_price,provider,created_at) VALUES (?,?,?,?,?,?)''',
                        (bad['symbol'], item['quote_at'], item['bid_price'], item['ask_price'], 'fixture', item['quote_at']))
        self.benchmarks()
        report = self.collect_report(run_id)
        self.assertEqual(promote_champion(report['report_id'], db_path=self.db)['champion_run_id'], run_id)
        self.assertTrue(champion_allows({'execution_version': self.manifest}, db_path=self.db))
        self.assertFalse(champion_allows({'execution_version': {'pipeline_version': 'other'}}, db_path=self.db))
        promote_champion(report['report_id'], db_path=self.db)
        with closing(get_connection(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM research_champion').fetchone()[0], 1)

    def test_environment_enforce_alone_cannot_activate_a_gate(self):
        result = guard_policy(ContractPolicy(liquidity_mode='enforce'), {'liquidity_mode': 'LIQUIDITY'}, db_path=self.db)
        self.assertEqual(result.liquidity_mode, 'shadow')

    def test_shared_liquidity_mode_needs_selection_and_execution_proofs(self):
        with patch.dict('os.environ', {'V2_LIQUIDITY_MODE': 'enforce'}), patch(
                'services.research_promotion.approved', side_effect=lambda stage, *args, **kwargs: stage == 'LIQUIDITY'):
            self.assertEqual(ContractPolicy.from_env().liquidity_mode, 'shadow')

    def test_shadow_challenger_cannot_enter_capital_path(self):
        self.assertFalse(champion_allows({'research_run_id': 'challenger'}, db_path=self.db))

    def test_unavailable_champion_database_cannot_grant_capital_authority(self):
        self.assertFalse(champion_allows({}, db_path=Path(self.temp.name) / 'missing.db'))

    def test_promotion_report_tampering_fails_closed(self):
        run_id = self.run_fixture()
        self.candidate()
        report = self.collect_report(run_id)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE research_reports SET report_json='{}'")
        with self.assertRaises(ValueError):
            promote_gate(report['report_id'], 'LIQUIDITY', db_path=self.db)


if __name__ == '__main__':
    unittest.main()
