"""The pick score is logged on every manager decision; nothing orders or blocks on it yet."""
from contextlib import closing
import json
import unittest

from db_common import get_connection
from market_db_modules.history_records import record_manager_decision_history
from services.gates.entry_extension_gate import evaluate_entry_extension_gate
from services.research.pick_score import PICK_SCORE_VERSION, compute_pick_score
from tests.db_fixtures import temporary_database


def manager_result(decision='call', *, move=5.0, closes=(100, 101, 102, 103, 104), price=105.0, risk='low',
                   relative_20d=4.0, remaining=0.8):
    market = {'current_stock_price': {'latest_trade_price': price},
              'underlying_price_history': {
                  'recent_closes': list(closes),
                  'technical_indicators': {'relative_strength': {'vs_spy': {'returns': {
                      '20d': {'relative_return_pct': relative_20d}}}}}}}
    recommendation = {'decision': decision, 'overextension_risk': risk, 'thesis_confidence': 'high',
                      'timing_confidence': 'low', 'execution_confidence': 'medium'}
    context = {'catalyst_gates': {'FRESHNESS': {'metrics': {'remaining_information_value': remaining}}}}
    result = {'recommendation': recommendation, 'market_context': market, 'candidate_context': context,
              'company': {'trend_quality': {'5d_return_pct': move}}}
    result['entry_extension_gate'] = evaluate_entry_extension_gate(result)
    return result


class PickScoreTests(unittest.TestCase):
    def test_a_fresh_low_risk_call_with_strength_scores_from_all_three_parts(self):
        score = compute_pick_score(manager_result())

        self.assertEqual(score['version'], PICK_SCORE_VERSION)
        self.assertEqual(score['inputs']['prior_5d_move_pct'], 5.0)
        self.assertEqual(score['components'], {'extension': 1.0, 'freshness': 0.8, 'relative_strength': 0.7})
        self.assertAlmostEqual(score['score'], (1.0 + 0.8 + 0.7) / 3)
        self.assertEqual(score['confidence'], {'thesis_confidence': 'high', 'timing_confidence': 'low',
                                               'execution_confidence': 'medium'})
        self.assertEqual(score['use'], 'log_only')

    def test_an_extended_move_with_overextension_risk_scores_zero_on_extension(self):
        score = compute_pick_score(manager_result(risk='high'))

        self.assertEqual(score['components']['extension'], 0.0)

    def test_a_put_reads_moves_and_strength_in_its_own_direction(self):
        score = compute_pick_score(manager_result('put', risk='high', relative_20d=-30.0))

        self.assertEqual(score['inputs']['prior_5d_move_pct'], -5.0)
        self.assertEqual(score['components']['extension'], 1.0)
        self.assertEqual(score['components']['relative_strength'], 1.0)

    def test_the_extension_part_is_the_entry_extension_verdict(self):
        result = manager_result(move=3.0, risk='medium')

        score = compute_pick_score(result)

        self.assertFalse(result['entry_extension_gate']['would_allow'])
        self.assertEqual(score['inputs']['prior_5d_move_pct'], 3.0)
        self.assertEqual(score['components']['extension'], 0.0)

    def test_the_extension_part_follows_the_recorded_verdict_not_its_own_reading(self):
        result = manager_result(move=-2.0, risk='low')
        result['entry_extension_gate'] = {**result['entry_extension_gate'], 'would_allow': False}

        self.assertEqual(compute_pick_score(result)['components']['extension'], 0.0)

    def test_no_extension_verdict_leaves_the_extension_part_unknown(self):
        result = manager_result()
        del result['entry_extension_gate']

        self.assertIsNone(compute_pick_score(result)['components']['extension'])

    def test_missing_inputs_drop_out_of_the_mean(self):
        result = manager_result(move=None, risk=None, relative_20d=None, remaining=None, closes=())
        result['recommendation'].pop('overextension_risk')

        score = compute_pick_score(result)

        self.assertIsNone(score['score'])
        self.assertEqual(score['components'], {'extension': None, 'freshness': None, 'relative_strength': None})

    def test_a_non_directional_decision_has_no_score(self):
        self.assertIsNone(compute_pick_score(manager_result('hold'))['score'])


class PickScorePipelineTests(unittest.TestCase):
    def test_post_manager_gates_attach_the_score_and_history_stores_it(self):
        from unittest.mock import patch
        from agentCallers import decision_pipeline

        def passthrough(result, *args, **kwargs):
            return result

        with patch.multiple(decision_pipeline, record_directed_absorption=passthrough,
                            apply_option_hurdle_to_manager_result=passthrough, prepare_scenario_inputs=passthrough,
                            apply_deterministic_option_selection=passthrough, record_scenario_selection=passthrough,
                            apply_market_regime_gate_to_manager_result=passthrough,
                            evaluate_evidence_thesis_gate=lambda *a, **k: None, apply_trade_economics=passthrough):
            result = decision_pipeline._apply_post_manager_gates(
                manager_result(), candidate_context=None, cycle_version=None, thesis_policy=None,
                economics_policy=None)
            with patch.object(decision_pipeline, 'record_manager_decision_history', lambda payload: payload):
                stored = decision_pipeline._record_manager_decision_history_for_result(result)

        self.assertEqual(result['pick_score']['version'], PICK_SCORE_VERSION)
        self.assertEqual(stored['manager_output_json']['pick_score'], result['pick_score'])


class PickScoreHistoryTests(unittest.TestCase):
    def test_the_score_and_confidence_components_are_columns_on_the_decision(self):
        db_path = temporary_database(self)
        result = manager_result()
        output = {'recommendation': result['recommendation'], 'pick_score': compute_pick_score(result)}

        decision_id = record_manager_decision_history(
            {'symbol': 'AAA', 'decision_run_at': '2026-11-02T15:00:00+00:00', 'manager_decision': 'call',
             'manager_output_json': output}, db_path=db_path)

        with closing(get_connection(db_path)) as conn:
            row = dict(conn.execute('SELECT thesis_confidence, timing_confidence, execution_confidence, pick_score, '
                                    'pick_score_version FROM manager_decision_history WHERE id=?',
                                    (decision_id,)).fetchone())
        self.assertEqual(row, {'thesis_confidence': 'high', 'timing_confidence': 'low',
                               'execution_confidence': 'medium', 'pick_score': output['pick_score']['score'],
                               'pick_score_version': PICK_SCORE_VERSION})

    def test_adding_the_columns_backfills_confidence_from_stored_output(self):
        from market_db_modules.bootstrap import _ensure_pick_score_columns
        db_path = temporary_database(self)
        with closing(get_connection(db_path)) as conn, conn:
            conn.execute("INSERT INTO manager_decision_history (id, decision_run_at) VALUES (7, '2026-10-01')")
            conn.execute('INSERT INTO manager_decision_payloads VALUES (7, NULL, ?)',
                         (json.dumps({'recommendation': {'thesis_confidence': 'low'}}),))
            for column in ('thesis_confidence', 'timing_confidence', 'execution_confidence', 'pick_score',
                           'pick_score_version'):
                conn.execute(f'ALTER TABLE manager_decision_history DROP COLUMN {column}')
            _ensure_pick_score_columns(conn)
            _ensure_pick_score_columns(conn)
            row = conn.execute('SELECT thesis_confidence, timing_confidence FROM manager_decision_history '
                               'WHERE id=7').fetchone()

        self.assertEqual(tuple(row), ('low', None))


if __name__ == '__main__':
    unittest.main()
