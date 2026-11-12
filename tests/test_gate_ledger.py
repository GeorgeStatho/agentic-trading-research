"""The gate ledger: every recorded gate and two plain tags scored on the 5-day residual move, per regime and model."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import unittest

from db_common import get_connection
from market_db_modules.candidate_lifecycle import record_candidate_snapshot
from market_db_modules.gate_ledger import gate_ledger
from tests.db_fixtures import temporary_database

NOW = datetime.now(timezone.utc)


class GateLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = temporary_database(self)
        self.count = 0

    def decision(self, side, move, *, gates=None, prior_5d=0.0, overextension='low', regime='range', model='m1',
                 verdict_rows=(), days_ago=1):
        """One manager decision with its candidate's gate checkpoints and its resolved 5-day residual move."""
        self.count += 1
        candidate_id = f'candidate-{self.count}'
        decided_at = (NOW - timedelta(days=days_ago)).isoformat()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO candidates (candidate_id, symbol, created_at, catalyst_ids_json, article_ids_json, "
                         "pipeline_version_json) VALUES (?, 'MSFT', ?, '[]', '[]', '{}')", (candidate_id, decided_at))
            decision_id = conn.execute(
                'INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision, candidate_id) '
                "VALUES ('MSFT', ?, ?, ?)", (decided_at, side, candidate_id)).lastrowid
            conn.execute('INSERT INTO manager_decision_payloads (manager_decision_history_id, manager_input_json, '
                         'manager_output_json) VALUES (?, ?, ?)',
                         (decision_id, json.dumps({'company': {'trend_quality': {'5d_return_pct': prior_5d}},
                                                   'replay_metadata': {'model': model}}),
                          json.dumps({'recommendation': {'overextension_risk': overextension},
                                      'market_regime_gate': {'regime_state': regime}})))
            if move is not None:
                conn.execute('INSERT INTO candidate_price_observations (candidate_id, catalyst_id, observed_at, bucket, '
                             'snapshot_json, forward_raw_residual_return, forward_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                             (candidate_id, 'catalyst:1', decided_at, 'b',
                              json.dumps({'direction': 'positive' if side == 'call' else 'negative'}), move, decided_at))
            for gate_name, policy_version, allowed in verdict_rows:
                conn.execute('INSERT INTO decision_gate_verdicts (manager_decision_history_id, gate_name, would_allow, '
                             "mode, policy_version, verdict_json) VALUES (?, ?, ?, 'shadow', ?, '{}')",
                             (decision_id, gate_name, int(allowed), policy_version))
        context = {'candidate_id': candidate_id, 'pipeline_version': {'pipeline_version': 'fixture'}}
        for stage, allowed in (gates or {}).items():
            record_candidate_snapshot(context, stage, {'would_allow': allowed, 'policy_version': f'{stage}-v1'},
                                      db_path=self.db)
        return decision_id

    def stage(self, name, **kwargs):
        return gate_ledger(days=60, db_path=self.db, **kwargs)['stages'][name]

    def test_a_gate_is_scored_by_the_move_in_the_traded_direction_and_unknown_is_counted_apart(self):
        self.decision('call', .04, gates={'ENTRY_EXTENSION': True})
        self.decision('call', -.02, gates={'ENTRY_EXTENSION': False})
        self.decision('put', -.03, gates={'ENTRY_EXTENSION': True})
        self.decision('call', .01, gates={'ENTRY_EXTENSION': None})
        self.decision('call', .05)
        cell = self.stage('ENTRY_EXTENSION[ENTRY_EXTENSION-v1]')
        self.assertEqual((cell['passed'], cell['rejected'], cell['unknown']), (2, 1, 1))
        self.assertAlmostEqual(cell['mean_move_passed'], .035)
        self.assertAlmostEqual(cell['mean_move_rejected'], -.02)
        self.assertAlmostEqual(cell['gate_value'], .02)
        self.assertAlmostEqual(cell['unknown_rate'], .25)

    def test_gate_value_is_the_proofs_losses_avoided_minus_profits_sacrificed(self):
        self.decision('call', .04, gates={'ENTRY_EXTENSION': True})
        self.decision('call', -.03, gates={'ENTRY_EXTENSION': False})
        self.decision('call', .01, gates={'ENTRY_EXTENSION': False})
        cell = self.stage('ENTRY_EXTENSION[ENTRY_EXTENSION-v1]')
        self.assertAlmostEqual(cell['losses_avoided'], .03)
        self.assertAlmostEqual(cell['profits_sacrificed'], .01)
        self.assertAlmostEqual(cell['gate_value'], .02)

    def test_unresolved_moves_count_but_do_not_enter_the_means(self):
        self.decision('call', .04, gates={'OPTION_HURDLE': True})
        self.decision('call', None, gates={'OPTION_HURDLE': False})
        cell = self.stage('OPTION_HURDLE[OPTION_HURDLE-v1]')
        self.assertEqual((cell['passed'], cell['rejected'], cell['rejected_resolved']), (1, 1, 0))
        self.assertIsNone(cell['mean_move_rejected'])
        self.assertIsNone(cell['gate_value'])

    def test_every_recorded_gate_stage_appears_without_being_named(self):
        self.decision('call', .01, gates={'TRADE_ECONOMICS': None, 'OPTION_HURDLE': None, 'SOME_NEW_GATE': True})
        stages = gate_ledger(days=60, db_path=self.db)['stages']
        self.assertTrue({'TRADE_ECONOMICS[TRADE_ECONOMICS-v1]', 'OPTION_HURDLE[OPTION_HURDLE-v1]',
                         'SOME_NEW_GATE[SOME_NEW_GATE-v1]'} <= set(stages))
        self.assertEqual(stages['TRADE_ECONOMICS[TRADE_ECONOMICS-v1]']['unknown_rate'], 1.0)

    def test_a_stored_verdict_without_a_checkpoint_is_listed_once_per_policy_version(self):
        self.decision('call', .02, verdict_rows=[('market_regime_gate', 'market-regime-v3-scope', True)])
        self.decision('call', -.01, gates={'REGIME': False}, verdict_rows=[('market_regime_gate', 'REGIME-v1', False)])
        stages = gate_ledger(days=60, db_path=self.db)['stages']
        self.assertEqual((stages['REGIME[market-regime-v3-scope]']['passed'], stages['REGIME[REGIME-v1]']['rejected']),
                         (1, 1))

    def test_overextension_and_prior_move_tags_are_scored_as_gates(self):
        self.decision('call', .04, prior_5d=-1.0, overextension='low')
        self.decision('call', -.02, prior_5d=5.0, overextension='high')
        self.decision('put', -.03, prior_5d=4.0, overextension='low')
        tags = gate_ledger(days=60, db_path=self.db)['stages']
        low = tags['overextension_risk_low']
        self.assertEqual((low['passed'], low['rejected']), (2, 1))
        self.assertAlmostEqual(low['gate_value'], .02)
        prior = tags['prior_5d_move_not_with_trade']
        # A put after a rise has a prior move of -4 in its own direction.
        self.assertEqual((prior['passed'], prior['rejected']), (2, 1))
        self.assertAlmostEqual(prior['mean_move_rejected'], -.02)

    def test_cells_are_split_by_regime_and_manager_model(self):
        self.decision('call', .04, gates={'ENTRY_EXTENSION': True}, regime='range', model='m1')
        self.decision('call', -.02, gates={'ENTRY_EXTENSION': False}, regime='range', model='m2')
        self.decision('call', .03, gates={'ENTRY_EXTENSION': True}, regime='stressed', model='m2')
        cell = self.stage('ENTRY_EXTENSION[ENTRY_EXTENSION-v1]')
        self.assertEqual(sorted(cell['by_regime']), ['range', 'stressed'])
        self.assertEqual((cell['by_regime']['range']['passed'], cell['by_regime']['range']['rejected']), (1, 1))
        self.assertAlmostEqual(cell['by_regime']['range']['gate_value'], .02)
        self.assertEqual(cell['by_model']['m2']['passed'], 1)
        self.assertEqual(cell['by_model']['m2']['rejected'], 1)

    def test_decisions_older_than_the_window_are_left_out(self):
        self.decision('call', .04, gates={'ENTRY_EXTENSION': True}, days_ago=90)
        result = gate_ledger(days=60, db_path=self.db)
        self.assertEqual((result['decisions'], 'ENTRY_EXTENSION[ENTRY_EXTENSION-v1]' in result['stages']), (0, False))


if __name__ == '__main__':
    unittest.main()
