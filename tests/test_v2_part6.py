from contextlib import closing
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import logging
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database
from market_db_modules.forward_evaluation import forward_status
from market_db_modules.research_runs import start_run, collect_run, collect_active_runs
from market_db_modules.research_reports import (
    monthly_report,
    promote_gate,
    promote_champion,
)
from services.gates.contract_policy import ContractPolicy
from services.research.research_contract import (
    ResearchPolicy,
    assert_information_cutoff,
    canonical,
    utc,
)
from services.research.research_promotion import approved, guard_policy, champion_allows, champion_check
from services.research.shadow_portfolio import simulate_option
from services.trading.order_candidates import OrderCandidateBuilder
from services.trading.trade_executor import TradeExecutionSession, OptionTradeExecutor

HARD_BLOCKS = {'premium_cap': ('EXECUTION', 'premium_per_trade_cap'),
               'no_contract': ('CONTRACT_SELECTION', 'continuation_confirmed_but_no_eligible_contract')}
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
    def test_missing_and_future_timestamps_are_rejected(self):
        for value in (None, END):
            with self.subTest(value=value), self.assertRaises(ValueError):
                assert_information_cutoff([value], ENTRY)

    def test_a_provider_clock_slightly_ahead_of_ours_is_not_future_information(self):
        # Exchange quote times come from the provider's clock; ours ran ~0.5-1 s slow on the live host.
        assert_information_cutoff(['2026-09-02T20:00:01.100000+00:00'], ENTRY)
        with self.assertRaises(ValueError):
            assert_information_cutoff(['2026-09-02T20:00:06+00:00'], ENTRY)

    def test_invalid_policy_cannot_lower_promotion_floor(self):
        for fields in ({'minimum_promotion_samples': 99}, {'holding_days': 0}, {'fee_per_contract_side': float('nan')}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                ResearchPolicy(**fields)


class ShadowTests(unittest.TestCase):
    def simulate(self, history=None, **kwargs):
        return simulate_option(contract(), history if history is not None else quotes(), cutoff=ENTRY, as_of=END, policy=POLICY, **kwargs)

    def test_spread_fees_horizon_and_dollar_days(self):
        result = self.simulate()
        self.assertAlmostEqual(result['hypothetical_pnl'], 8.7)
        self.assertAlmostEqual(result['deployed_dollar_days'], 200.65 * 5)
        self.assertEqual(result['hypothetical_exit']['reason'], 'catalyst_expired')

    def test_missing_entry_quote_is_not_a_free_entry(self):
        self.assertEqual(self.simulate(quotes()[1:])['status'], 'missing_entry_quote')


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
            return champion_check(candidate, db_path=self.db)

        executor = OptionTradeExecutor(settings=SimpleNamespace(), trading_gateway=Mock(),
                                       logger=logging.getLogger('fixture'))
        session = TradeExecutionSession(available_buying_power=20000, max_deployable_buying_power=10000,
                                        remaining_deployable_buying_power=10000, base_order_qty=1)
        with patch('services.research.research_promotion.champion_check', spy), self.assertLogs('fixture', 'ERROR'):
            result = executor.execute_candidate(trading_client=Mock(), candidate=built, session=session)
        self.assertFalse(result['submitted'])
        self.assertEqual(result['portfolio_guardrail'], 'research_champion_only')
        self.assertEqual(result['champion_guard']['reason'], 'research_candidate')
        # The refusal is part of the cycle's record, not a silent early return.
        self.assertEqual(session.executions, [result])
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
        # Proofs bind to the run's per-file source hashes, as a worker's manifest records them.
        from services.core.trading_path import trading_path_hashes
        self.manifest = {'pipeline_version': 'fixture-v1', 'gate_config': {'contracts': asdict(ContractPolicy())},
                         'source_hashes': trading_path_hashes()}

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

    def test_changed_snapshot_hash_is_rejected(self):
        run_id = self.run_fixture()
        self.candidate()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE candidate_snapshots SET snapshot_json='{}'")
        with self.assertRaises(ValueError):
            collect_run(run_id, as_of=END, db_path=self.db)

    def test_completed_shadow_outcome_cannot_be_rewritten_by_quote_backfill(self):
        run_id = self.run_fixture()
        self.candidate()
        before = self.collect_report(run_id)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('UPDATE historical_option_quotes SET bid_price=0 WHERE quote_at>?', (ENTRY,))
        self.assertEqual(self.collect_report(run_id), before)

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

    def test_quote_stamped_a_second_after_capture_still_enrolls(self):
        run_id = self.run_fixture()
        self.candidate()
        with closing(get_connection(self.db)) as conn, conn:
            row = conn.execute("SELECT id,snapshot_json,information_cutoff_time FROM candidate_snapshots WHERE stage='post_contract_selection'").fetchone()
            envelope = json.loads(row['snapshot_json'])
            ahead = (utc(row['information_cutoff_time']) + timedelta(seconds=1)).isoformat()
            envelope['payload']['market_context']['timestamp'] = ahead
            serialized = canonical(envelope)
            from hashlib import sha256
            conn.execute('UPDATE candidate_snapshots SET snapshot_json=?,snapshot_hash=? WHERE id=?',
                         (serialized, sha256(serialized.encode()).hexdigest(), row['id']))
        self.assertEqual(collect_run(run_id, as_of=END, db_path=self.db)['candidates'], 1)

    def set_market_timestamp(self, value):
        with closing(get_connection(self.db)) as conn, conn:
            row = conn.execute("SELECT id,snapshot_json FROM candidate_snapshots WHERE stage='post_contract_selection'").fetchone()
            envelope = json.loads(row['snapshot_json'])
            envelope['payload']['market_context']['timestamp'] = value
            serialized = canonical(envelope)
            from hashlib import sha256
            conn.execute('UPDATE candidate_snapshots SET snapshot_json=?,snapshot_hash=? WHERE id=?',
                         (serialized, sha256(serialized.encode()).hexdigest(), row['id']))

    def test_a_cohort_paused_by_a_candidate_says_which_candidate_and_why(self):
        run_id = self.run_fixture()
        candidate_id = self.candidate()
        self.set_market_timestamp(END)
        result, = collect_active_runs(as_of=END, db_path=self.db)
        self.assertEqual(result['status'], 'paused')
        pause, = forward_status(db_path=self.db)['paused_runs']
        self.assertEqual((pause['run_id'], pause['candidate_id']), (run_id, candidate_id))
        self.assertIn(END, pause['reason'])
        self.assertIn(ENTRY, pause['reason'])
        self.assertEqual(pause['paused_at'], END)

    def test_a_pause_clears_once_the_cohort_collects_again(self):
        self.run_fixture()
        self.candidate()
        self.set_market_timestamp(END)
        collect_active_runs(as_of=END, db_path=self.db)
        self.set_market_timestamp(ENTRY)
        collect_active_runs(as_of=END, db_path=self.db)
        self.assertEqual(forward_status(db_path=self.db)['paused_runs'], [])

    def test_a_failing_pause_write_does_not_stop_the_other_runs(self):
        import sqlite3
        first, second = self.run_fixture(), self.run_fixture(2)
        collected = []

        def collect(run_id, **kwargs):
            if run_id == first:
                raise ValueError('Candidate candidate-000: bad input')
            collected.append(run_id)
            return {'run_id': run_id}
        with patch('market_db_modules.research_runs.collect_run', side_effect=collect), \
                patch('market_db_modules.research_runs._record_pause',
                      side_effect=sqlite3.OperationalError('database is locked')):
            results = collect_active_runs(as_of=END, db_path=self.db)
        self.assertEqual((collected, len(results)), ([second], 2))

    def test_a_run_that_was_never_paused_writes_no_clearing(self):
        self.run_fixture()
        with patch('market_db_modules.research_runs.collect_run', return_value={}), \
                patch('market_db_modules.research_runs._clear_pause') as clear:
            collect_active_runs(as_of=END, db_path=self.db)
        clear.assert_not_called()

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
        # A deploy or a later gate promotion changes code hashes and modes, not the tested configuration.
        redeployed = {**self.manifest, 'pipeline_version': 'v2:redeployed', 'source_hashes': {'services/x.py': 'new'},
                      'gate_config': {'contracts': asdict(ContractPolicy(liquidity_mode='enforce'))}}
        self.assertTrue(champion_allows({'execution_version': redeployed}, db_path=self.db))
        retuned = {**self.manifest, 'gate_config': {'contracts': asdict(ContractPolicy(maximum_spread_fraction=.2))}}
        self.assertEqual(champion_check({'execution_version': retuned}, db_path=self.db),
                         (False, 'champion_manifest_mismatch', ['gate_config.contracts.maximum_spread_fraction']))
        promote_champion(report['report_id'], db_path=self.db)
        with closing(get_connection(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM research_champion').fetchone()[0], 1)

    def test_environment_enforce_alone_cannot_activate_a_gate(self):
        result = guard_policy(ContractPolicy(liquidity_mode='enforce'), {'liquidity_mode': 'LIQUIDITY'}, db_path=self.db)
        self.assertEqual(result.liquidity_mode, 'shadow')

    def test_shared_liquidity_mode_needs_selection_and_execution_proofs(self):
        with patch.dict('os.environ', {'V2_LIQUIDITY_MODE': 'enforce'}), patch(
                'services.research.research_promotion.approved', side_effect=lambda stage, *args, **kwargs: stage == 'LIQUIDITY'):
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



class GateEvidenceBasisTests(unittest.TestCase):
    @staticmethod
    def row(verdict, move, *, manager=None, catalyst='positive'):
        return {'gates': {'PROVENANCE': verdict}, 'residual_return': move,
                'features': {'manager_direction': manager, 'catalyst_direction': catalyst}}

    def test_move_scoring_signs_by_the_proposed_side_and_counts_no_trade_candidates(self):
        from services.research.shadow_portfolio import evaluate_gate_on_moves
        rows = [self.row(True, .03), self.row(False, -.02), self.row(False, .01),
                # The manager's put outranks the catalyst's direction: a falling stock is a winner it rejected.
                self.row(False, -.04, manager='put'),
                self.row(False, .05, catalyst='unknown'), self.row(None, .02), self.row(True, None)]
        gate = evaluate_gate_on_moves(rows, 'PROVENANCE')
        self.assertEqual((gate['n_seen'], gate['n_no_direction'], gate['n_unknown_verdicts']), (5, 1, 1))
        self.assertEqual((gate['n_passed'], gate['n_rejected'], gate['n_evaluable']), (2, 3, 4))
        self.assertAlmostEqual(gate['losses_avoided'], .02)
        self.assertAlmostEqual(gate['profits_sacrificed'], .05)
        self.assertAlmostEqual(gate['gate_value'], -.03)
        self.assertAlmostEqual(gate['coverage'], .8)
        self.assertFalse(gate['coverage_complete'])

    def test_every_gate_file_is_hashed_and_a_proof_survives_unrelated_deploys(self):
        from services.core.trading_path import trading_path_files, trading_path_hashes
        from services.research.research_promotion import GATE_FILES, gate_code_hash
        missing = {name for files in GATE_FILES.values() for name in files} - set(trading_path_files())
        self.assertEqual(missing, set())
        hashes = trading_path_hashes()
        unrelated = {**hashes, 'Python Scripts/agentCallers/decision_pipeline.py': 'changed'}
        self.assertEqual(gate_code_hash('PROVENANCE', unrelated), gate_code_hash('PROVENANCE', hashes))
        self.assertNotEqual(gate_code_hash('SCENARIO_SELECTION', unrelated), gate_code_hash('SCENARIO_SELECTION', hashes))
        gate_edit = {**hashes, 'Python Scripts/services/gates/catalyst_gates.py': 'changed'}
        self.assertNotEqual(gate_code_hash('FRESHNESS', gate_edit), gate_code_hash('FRESHNESS', hashes))

    def test_production_cohorts_span_code_only_deploys_but_not_threshold_changes(self):
        from market_db_modules.research_runs import same_strategy
        from services.research.research_promotion import champion_identity
        run = {'pipeline_version': 'v2:a', 'source_hashes': {'x.py': '1'}, 'gate_config': {'regime': {'maximum_opposition': .75}}}
        identity = champion_identity(run)
        deployed = {**run, 'pipeline_version': 'v2:b', 'source_hashes': {'x.py': '2'}}
        retuned = {**run, 'gate_config': {'regime': {'maximum_opposition': .5}}}
        self.assertTrue(same_strategy(deployed, run, 'production', identity))
        self.assertFalse(same_strategy(retuned, run, 'production', identity))
        self.assertFalse(same_strategy(deployed, run, 'challenger', identity))


class PooledGateProofTests(unittest.TestCase):
    """A move-basis gate's proof pools every run's rows produced by its current code and policy."""
    REPORTED = '2026-10-01T20:00:00+00:00'

    def setUp(self):
        from services.core.pipeline_version import gate_config
        from services.core.trading_path import trading_path_hashes
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'research.db'
        initialize_database(db_path=self.db)
        self.hashes = trading_path_hashes()
        self.manifest = {'pipeline_version': 'fixture-v1', 'gate_config': gate_config(), 'source_hashes': self.hashes}
        self.count = 0

    def new_run(self, manifest=None):
        return start_run(manifest or self.manifest, target_candidates=10000, now=START, db_path=self.db)['run_id']

    def enrol(self, run_id, *, passed, move, symbol=None, at=ENTRY, side='call', regime=None, hashes=None,
              stage='REGIME', outcome=None, selection_source='recorded_production'):
        from services.research.research_contract import fingerprint
        from services.research.research_promotion import gate_code_hashes
        self.count += 1
        candidate_id = f'candidate-{self.count:04d}'
        inputs = {'candidate_id': candidate_id, 'symbol': symbol or f'SYM{self.count}', 'created_at': at,
                  'information_cutoff_time': at, 'selection_source': selection_source,
                  'features': {'gates': {stage: passed}, 'manager_direction': side,
                               'regime': regime or ('range' if self.count % 2 else 'trend_up')},
                  'gate_code': gate_code_hashes(hashes or self.hashes)}
        forward = (utc(at) + timedelta(days=5)).isoformat()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('''INSERT OR IGNORE INTO candidates(candidate_id,symbol,created_at,catalyst_ids_json,article_ids_json,pipeline_version_json)
                VALUES (?,?,?,?,?,?)''', (candidate_id, inputs['symbol'], at, '[]', '[]', canonical(self.manifest)))
            conn.execute('INSERT INTO research_candidates VALUES (?,?,?,?,?)',
                         (run_id, candidate_id, at, fingerprint(inputs), canonical(inputs)))
            conn.execute('''INSERT INTO candidate_price_observations
                (candidate_id,catalyst_id,observed_at,bucket,snapshot_json,forward_residual_return,forward_at,forward_checked_at,forward_raw_residual_return)
                VALUES (?,?,?,?,?,?,?,?,?)''', (candidate_id, 'catalyst', at, 'b', '{}', move, forward, forward, move))
            if outcome in HARD_BLOCKS:
                conn.execute('INSERT INTO candidate_rejections (candidate_id,symbol,stage,reason_code,occurred_at) '
                             'VALUES (?,?,?,?,?)', (candidate_id, inputs['symbol'], *HARD_BLOCKS[outcome], at))
            elif outcome == 'fits':
                conn.execute('''INSERT INTO candidate_snapshots (candidate_id,stage,captured_at,information_cutoff_time,
                    pipeline_version,snapshot_version,snapshot_hash,snapshot_json) VALUES (?,?,?,?,?,?,?,?)''',
                             (candidate_id, 'order_submitted', at, at, 'fixture-v1', 'v', 'h', '{}'))
        return candidate_id

    def tradability(self, run_id, *, blocked, fits, passed=110, missing=0, declined=0):
        for index in range(passed):
            self.enrol(run_id, passed=True, move=None, stage='TRADABILITY', outcome='fits')
        for outcome, n in (('premium_cap', blocked), ('fits', fits), (None, missing)):
            for index in range(n):
                self.enrol(run_id, passed=False, move=None, stage='TRADABILITY', outcome=outcome)
        for index in range(declined):
            self.enrol(run_id, passed=False, move=None, stage='TRADABILITY', side='hold')

    def useful_gate(self, run_id, n, **kwargs):
        for index in range(n):
            self.enrol(run_id, passed=index % 2 == 0, move=.02 if index % 2 == 0 else -.02, **kwargs)

    def report(self, stage='REGIME', as_of=REPORTED):
        from market_db_modules.research_reports import pooled_gate_report
        return pooled_gate_report(stage, as_of=as_of, db_path=self.db)

    def test_rows_from_runs_across_deploys_pool_into_one_promotable_proof(self):
        from services.gates.market_regime_gate import MarketRegimePolicy
        deployed = {**self.manifest, 'pipeline_version': 'fixture-v2'}
        self.useful_gate(self.new_run(), 60)
        self.useful_gate(self.new_run(deployed), 60)
        report = self.report()
        gate, = report['gates']
        self.assertEqual(len(report['run_ids']), 2)
        self.assertEqual((gate['n_evaluable'], gate['n_passed'], gate['n_rejected']), (120, 60, 60))
        self.assertEqual(gate['regimes'], ['range', 'trend_up'])
        self.assertGreater(gate['gate_value'], 0)
        self.assertFalse(approved('REGIME', MarketRegimePolicy(), db_path=self.db))
        self.assertEqual(guard_policy(MarketRegimePolicy(mode='enforce'), {'mode': 'REGIME'}, db_path=self.db).mode, 'shadow')
        proof = promote_gate(report['report_id'], 'REGIME', db_path=self.db)
        self.assertEqual(proof['sample_size'], 120)
        self.assertEqual(guard_policy(MarketRegimePolicy(mode='enforce'), {'mode': 'REGIME'}, db_path=self.db).mode, 'enforce')
        self.assertFalse(approved('REGIME', MarketRegimePolicy(maximum_opposition=.5), db_path=self.db))

    def test_rows_from_other_gate_code_or_policy_are_not_pooled(self):
        from services.gates.market_regime_gate import MarketRegimePolicy
        self.useful_gate(self.new_run(), 10)
        edited = {**self.hashes, 'Python Scripts/services/gates/market_regime_gate.py': 'changed'}
        self.useful_gate(self.new_run(), 10, hashes=edited)
        # A deploy that leaves the gate's files alone still pools.
        self.useful_gate(self.new_run(), 10, hashes={**self.hashes, 'Python Scripts/agentCallers/decision_pipeline.py': 'changed'})
        retuned = {**self.manifest, 'gate_config': {**self.manifest['gate_config'],
                                                    'regime': asdict(MarketRegimePolicy(maximum_opposition=.5))}}
        self.useful_gate(self.new_run(retuned), 10)
        report = self.report()
        self.assertEqual(report['gates'][0]['n_evaluable'], 20)
        self.assertEqual(report['n_other_code_or_policy'], 20)

    def test_repeated_asks_for_one_company_day_and_side_count_once(self):
        run_id = self.new_run()
        first = self.enrol(run_id, passed=True, move=.02, symbol='NVDA', at=ENTRY)
        self.enrol(run_id, passed=False, move=.02, symbol='NVDA', at='2026-09-02T20:30:00+00:00')
        put = self.enrol(run_id, passed=False, move=.02, symbol='NVDA', at='2026-09-02T20:45:00+00:00', side='put')
        later = self.enrol(run_id, passed=False, move=.02, symbol='NVDA', at='2026-09-03T20:00:00+00:00')
        # A second run enrolling the same candidate is the same ask.
        with closing(get_connection(self.db)) as conn, conn:
            row = conn.execute('SELECT * FROM research_candidates WHERE candidate_id=?', (first,)).fetchone()
            conn.execute('INSERT INTO research_candidates VALUES (?,?,?,?,?)',
                         (self.new_run(), first, row['information_cutoff_time'], row['input_hash'], row['input_json']))
        report = self.report()
        gate, = report['gates']
        self.assertEqual(report['candidate_ids'], [first, put, later])
        self.assertEqual((gate['n_passed'], gate['n_rejected']), (1, 2))
        self.assertEqual(report['n_duplicate_asks'], 2)

    def test_the_window_closes_a_horizon_before_the_report(self):
        run_id = self.new_run()
        self.enrol(run_id, passed=True, move=.02)
        self.enrol(run_id, passed=True, move=.02, at='2026-09-28T20:00:00+00:00')
        report = self.report()
        self.assertEqual(report['window_end'], '2026-09-26T20:00:00+00:00')
        self.assertEqual(report['gates'][0]['n_seen'], 1)

    def test_a_pooled_proof_keeps_the_sample_floor(self):
        self.useful_gate(self.new_run(), 99)
        with self.assertRaises(ValueError):
            promote_gate(self.report()['report_id'], 'REGIME', db_path=self.db)

    def test_a_pooled_proof_needs_two_regimes(self):
        self.useful_gate(self.new_run(), 120, regime='range')
        with self.assertRaises(ValueError):
            promote_gate(self.report()['report_id'], 'REGIME', db_path=self.db)

    def test_a_future_report_time_cannot_close_the_window(self):
        self.useful_gate(self.new_run(), 120)
        # It would close the window over outcomes nobody had seen yet.
        with self.assertRaises(ValueError):
            promote_gate(self.report(as_of='2099-01-01T00:00:00+00:00')['report_id'], 'REGIME', db_path=self.db)

    def test_only_move_basis_gates_pool(self):
        with self.assertRaises(ValueError):
            self.report('LIQUIDITY')

    def test_unfinished_rows_carry_only_rejections_and_are_not_sampled(self):
        run_id = self.new_run()
        self.enrol(run_id, passed=True, move=.02)
        self.enrol(run_id, passed=False, move=.02, selection_source='missing_or_unfinished_snapshots')
        report = self.report()
        self.assertEqual((report['gates'][0]['n_seen'], report['n_unfinished']), (1, 1))

    def test_a_pooled_proof_is_refused_once_the_evaluator_changed(self):
        self.useful_gate(self.new_run(), 120)
        report = self.report()
        with patch('services.research.research_versions.evaluator_source_hash', return_value='edited'):
            with self.assertRaisesRegex(ValueError, 're-run report-gate'):
                promote_gate(report['report_id'], 'REGIME', db_path=self.db)

    def test_tradability_pools_on_how_often_its_rejections_were_hard_blocked(self):
        from services.gates.tradability_gate import TradabilityPolicy
        self.tradability(self.new_run(), blocked=105, fits=5, missing=7)
        gate, = self.report('TRADABILITY')['gates']
        self.assertEqual(gate['basis'], 'hard_block_precision')
        self.assertEqual((gate['n_rejected'], gate['n_evaluable'], gate['n_blocked']), (117, 110, 105))
        self.assertAlmostEqual(gate['gate_value'], 105 / 110)
        promote_gate(self.report('TRADABILITY')['report_id'], 'TRADABILITY', db_path=self.db)
        self.assertTrue(approved('TRADABILITY', TradabilityPolicy.from_env(), db_path=self.db))

    def test_tradability_below_the_precision_floor_is_refused(self):
        self.tradability(self.new_run(), blocked=100, fits=10)
        with self.assertRaisesRegex(ValueError, 'precision'):
            promote_gate(self.report('TRADABILITY')['report_id'], 'TRADABILITY', db_path=self.db)

    def test_tradability_needs_outcomes_for_most_rejected_manager_trades(self):
        self.tradability(self.new_run(), blocked=110, fits=0, missing=20)
        gate, = self.report('TRADABILITY')['gates']
        self.assertAlmostEqual(gate['coverage'], 110 / 130)
        self.assertFalse(gate['coverage_complete'])
        with self.assertRaisesRegex(ValueError, 'coverage'):
            promote_gate(self.report('TRADABILITY')['report_id'], 'TRADABILITY', db_path=self.db)

    def test_tradability_rejections_the_manager_declined_do_not_lower_coverage(self):
        self.tradability(self.new_run(), blocked=110, fits=0, declined=50)
        gate, = self.report('TRADABILITY')['gates']
        self.assertEqual((gate['n_not_applicable'], gate['coverage'], gate['coverage_complete']), (50, 1.0, True))

    def test_tradability_below_the_sample_floor_is_refused(self):
        self.tradability(self.new_run(), blocked=99, fits=0)
        with self.assertRaises(ValueError):
            promote_gate(self.report('TRADABILITY')['report_id'], 'TRADABILITY', db_path=self.db)


class NewStageCheckpointTests(unittest.TestCase):
    def test_a_recorded_gate_checkpoint_reaches_frozen_inputs(self):
        from market_db_modules.candidate_lifecycle import record_candidate_snapshot
        from services.research.research_inputs import load_inputs
        from tests.db_fixtures import temporary_database
        db = temporary_database(self)
        with closing(get_connection(db)) as conn, conn:
            conn.execute("INSERT INTO candidates (candidate_id, symbol, created_at, catalyst_ids_json, article_ids_json, "
                         "pipeline_version_json) VALUES ('c1', 'MSFT', ?, '[]', '[]', ?)",
                         (ENTRY, json.dumps({'pipeline_version': 'fixture'})))
        context = {'candidate_id': 'c1', 'pipeline_version': {'pipeline_version': 'fixture'}}
        record_candidate_snapshot(context, 'ENTRY_EXTENSION', {'would_allow': False}, db_path=db)
        with closing(get_connection(db)) as conn:
            candidate = conn.execute("SELECT * FROM candidates WHERE candidate_id='c1'").fetchone()
            inputs = load_inputs(conn, candidate, '2099-01-01T00:00:00+00:00')
        self.assertIs(inputs['features']['gates']['ENTRY_EXTENSION'], False)


if __name__ == '__main__':
    unittest.main()
