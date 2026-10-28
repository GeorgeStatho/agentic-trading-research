"""Plan C slice C2: the attribution report (module and `research.py attribution`) and the Decision Autopsy field.

The report reads stored attributions, joined to their decision for the call direction. Persistence uses real temporary
databases; new modules are imported on use so each test fails on its own while they do not exist yet.
"""
from __future__ import annotations

from contextlib import closing, redirect_stdout
from datetime import datetime, timezone
import importlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from db_common import get_connection
from market_db_modules.history_records import record_manager_decision_history

import tests.test_decision_attribution as base
from tests.db_fixtures import temporary_database

RESEARCH_CLI = Path(__file__).resolve().parents[1] / 'scripts' / 'research.py'
OLD_COMPUTED_AT = '2026-03-10T00:00:00+00:00'


def report_module():
    return importlib.import_module('market_db_modules.decision_attribution_report')


def row(name, decision, quality, source, self_rating, h5, h10, *, failure='none', expression='not_traded', pnl=None):
    """A stored attribution. `h5` and `h10` are `(outcome, stock_return)`; a None return means no closes yet."""
    horizons = {'5': h5, '10': h10}
    signals = {} if source == 'thesis_check' else {'model_self_rating': {'evidence_quality': self_rating,
                                                                         'contradictions_present': False}}
    return {'name': name, 'decision': decision, 'failure': failure, 'record': {
        'attribution_version': 'decision-attribution-v1',
        'evidence': {'quality': quality, 'source': source, 'signals': signals},
        'direction': {'outcome': 'abstained' if decision == 'neither' else h5[0], 'primary_horizon': 5,
                      'horizon_outcomes': {key: outcome for key, (outcome, _) in horizons.items()},
                      'horizon_returns': {key: value for key, (_, value) in horizons.items() if value is not None},
                      'abs_move': {key: abs(value) for key, (_, value) in horizons.items() if value is not None}},
        'expression': {'outcome': expression, 'pnl_pct': pnl}, 'primary_failure': failure}}


# Returns are the stock's raw moves; a put is right when the stock falls. Signed return = direction sign * move.
ROWS = (
    row('A', 'call', 'strong', 'grounding_proxy', 'high', ('correct', 0.10), ('wrong', -0.03),
        failure='expression', expression='lost_despite_direction', pnl=-0.2),
    row('B', 'call', 'strong', 'grounding_proxy', 'high', ('wrong', -0.04), ('correct', 0.05), failure='forecast'),
    row('C', 'put', 'strong', 'thesis_check', None, ('correct', -0.06), ('correct', -0.02),
        expression='captured', pnl=0.5),
    row('D', 'call', 'weak', 'grounding_proxy', 'high', ('wrong', -0.02), ('correct', 0.01),
        failure='evidence', expression='lost_with_direction', pnl=-0.4),
    row('E', 'call', 'weak', 'grounding_proxy', 'low', ('flat', 0.001), ('correct', 0.02)),
    row('F', 'neither', 'adequate', 'grounding_proxy', 'low', ('abstained', 0.03), ('abstained', 0.06)),
    row('G', 'neither', 'adequate', 'grounding_proxy', 'low', ('abstained', 0.01), ('pending', None)),
    row('H', 'call', 'strong', 'grounding_proxy', 'low', ('pending', None), ('pending', None), failure='pending'),
)


class ReportDatabase(base.AttributionDatabase):
    def seed(self, rows=ROWS) -> None:
        for fixture in rows:
            decision_id = self.add_decision(fixture['decision'])
            store = base.store()
            store.upsert_attribution({**fixture['record'], 'manager_decision_history_id': decision_id},
                                     db_path=self.db_path)

    def report(self, **options) -> dict:
        return report_module().decision_attribution_report(db_path=self.db_path, **options)


class MatrixTests(ReportDatabase):
    def test_the_matrix_counts_every_stored_decision_once_whatever_the_horizon(self) -> None:
        self.seed()
        for horizon in (5, 10):
            with self.subTest(horizon=horizon):
                matrix = self.report(days=0, horizon=horizon)['matrix']
                cells = [cell['count'] for quality in matrix.values() for cell in quality['cells'].values()]
                self.assertEqual(sum(cells), len(ROWS))

    def test_cells_hold_the_count_and_the_mean_signed_return_at_the_chosen_horizon(self) -> None:
        self.seed()
        strong = self.report(days=0, horizon=5)['matrix']['strong']['cells']
        self.assertEqual({outcome: cell['count'] for outcome, cell in strong.items()},
                         {'correct': 2, 'wrong': 1, 'pending': 1})
        # A (call, +10%) and C (put, stock -6%) are both right: signed returns +0.10 and +0.06.
        self.assertAlmostEqual(strong['correct']['mean_signed_return'], 0.08)
        self.assertAlmostEqual(strong['wrong']['mean_signed_return'], -0.04)
        self.assertIsNone(strong['pending']['mean_signed_return'])

    def test_the_other_horizon_regrades_the_same_decisions(self) -> None:
        self.seed()
        strong = self.report(days=0, horizon=10)['matrix']['strong']['cells']
        self.assertEqual({outcome: cell['count'] for outcome, cell in strong.items()},
                         {'correct': 2, 'wrong': 1, 'pending': 1})
        self.assertAlmostEqual(strong['correct']['mean_signed_return'], 0.035)
        self.assertAlmostEqual(strong['wrong']['mean_signed_return'], -0.03)

    def test_a_row_of_the_matrix_has_its_hit_rate_over_graded_calls_with_a_bootstrap_error(self) -> None:
        self.seed()
        matrix = self.report(days=0, horizon=5)['matrix']
        # Graded = correct, wrong or flat; pending and abstained are not.
        self.assertEqual((matrix['strong']['graded'], matrix['strong']['count']), (3, 4))
        self.assertAlmostEqual(matrix['strong']['hit_rate'], 2 / 3)
        self.assertEqual(matrix['strong']['hit_rate_se'], base.attribution().bootstrap_se([1, 1, 0], n=1000, seed=0))
        self.assertEqual((matrix['weak']['hit_rate'], matrix['weak']['hit_rate_se']), (0.0, 0.0))
        self.assertEqual((matrix['adequate']['graded'], matrix['adequate']['hit_rate'], matrix['adequate']['hit_rate_se']),
                         (0, None, None))

    def test_abstentions_have_no_signed_return(self) -> None:
        self.seed()
        cell = self.report(days=0, horizon=5)['matrix']['adequate']['cells']['abstained']
        self.assertEqual((cell['count'], cell['mean_signed_return']), (2, None))


class BreakdownTests(ReportDatabase):
    def setUp(self) -> None:
        super().setUp()
        self.seed()

    def test_hit_rate_by_evidence_source(self) -> None:
        sources = self.report(days=0, horizon=5)['by_evidence_source']
        self.assertEqual((sources['thesis_check']['count'], sources['thesis_check']['hit_rate']), (1, 1.0))
        # Proxy: A right; B, D wrong; E flat; F, G abstained and H pending are not graded.
        self.assertEqual((sources['grounding_proxy']['count'], sources['grounding_proxy']['graded']), (7, 4))
        self.assertAlmostEqual(sources['grounding_proxy']['hit_rate'], 0.25)
        self.assertEqual(sources['grounding_proxy']['hit_rate_se'],
                         base.attribution().bootstrap_se([1, 0, 0, 0], n=1000, seed=0))

    def test_the_models_own_evidence_rating_is_checked_against_what_happened(self) -> None:
        for horizon, high, low in ((5, 1 / 3, 0.0), (10, 2 / 3, 1.0)):
            with self.subTest(horizon=horizon):
                calibration = self.report(days=0, horizon=horizon)['model_self_rating_calibration']
                self.assertEqual(calibration['high']['graded'], 3)
                self.assertAlmostEqual(calibration['high']['hit_rate'], high)
                self.assertAlmostEqual(calibration['low']['hit_rate'], low)
                self.assertEqual(calibration['high']['hit_rate_se'], base.attribution().bootstrap_se(
                    [1, 0, 0] if horizon == 5 else [1, 1, 0], n=1000, seed=0))

    def test_primary_failures_are_counted(self) -> None:
        counts = self.report(days=0)['primary_failure_counts']
        self.assertEqual({key: value for key, value in counts.items() if value},
                         {'none': 4, 'expression': 1, 'forecast': 1, 'evidence': 1, 'pending': 1})

    def test_expression_covers_traded_decisions_only(self) -> None:
        expression = self.report(days=0)['expression']
        self.assertEqual({key: expression.get(key, 0) for key in ('captured', 'lost_despite_direction', 'lost_with_direction')},
                         {'captured': 1, 'lost_despite_direction': 1, 'lost_with_direction': 1})
        self.assertNotIn('not_traded', expression)

    def test_abstained_moves_are_set_against_directional_ones(self) -> None:
        abstentions = self.report(days=0, horizon=5)['abstentions']
        self.assertEqual(abstentions['neither']['count'], 2)
        self.assertAlmostEqual(abstentions['neither']['mean_abs_move'], 0.02)
        self.assertAlmostEqual(abstentions['neither']['median_abs_move'], 0.02)
        # A .10, B .04, C .06, D .02, E .001: H has no closes yet.
        self.assertEqual(abstentions['directional']['count'], 5)
        self.assertAlmostEqual(abstentions['directional']['mean_abs_move'], 0.0442)
        self.assertAlmostEqual(abstentions['directional']['median_abs_move'], 0.04)


class ReportShapeTests(ReportDatabase):
    def test_the_report_is_deterministic_and_strict_json(self) -> None:
        self.seed()
        first, second = self.report(days=0, horizon=5), self.report(days=0, horizon=5)
        self.assertEqual(first, second)
        json.dumps(first, allow_nan=False)

    def test_an_empty_database_reports_nothing_without_failing(self) -> None:
        result = self.report(days=0, horizon=5)
        self.assertEqual((result['total'], result['matrix'], result['primary_failure_counts']), (0, {}, {}))
        json.dumps(result, allow_nan=False)

    def test_the_total_and_options_are_echoed(self) -> None:
        self.seed()
        result = self.report(days=0, horizon=10)
        self.assertEqual((result['total'], result['horizon'], result['days']), (len(ROWS), 10, 0))

    def test_days_keeps_recent_attributions_only_and_zero_keeps_all(self) -> None:
        old = self.add_decision('call')
        recent = self.add_decision('call', at=datetime.now(timezone.utc).isoformat())
        for decision_id in (old, recent):
            base.store().upsert_attribution(
                {**ROWS[0]['record'], 'manager_decision_history_id': decision_id}, db_path=self.db_path)
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute('UPDATE decision_attributions SET computed_at = ? WHERE manager_decision_history_id = ?',
                         (OLD_COMPUTED_AT, old))
        self.assertEqual((self.report(days=7)['total'], self.report(days=0)['total']), (1, 2))


class ResearchCliTests(ReportDatabase):
    def run_cli(self, *args) -> dict:
        spec = importlib.util.spec_from_file_location('research_cli', RESEARCH_CLI)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = io.StringIO()
        with patch.object(sys, 'argv', ['research.py', '--db', str(self.db_path), *args]), redirect_stdout(out):
            module.main()
        return json.loads(out.getvalue())

    def test_attribution_prints_the_report_as_json(self) -> None:
        self.seed()
        result = self.run_cli('attribution', '--days', '0', '--horizon', '10')
        self.assertEqual((result['total'], result['horizon']), (len(ROWS), 10))
        self.assertEqual(result, json.loads(json.dumps(self.report(days=0, horizon=10))))

    def test_it_is_read_only(self) -> None:
        self.seed()
        with closing(get_connection(self.db_path)) as conn:
            before = conn.execute('SELECT * FROM decision_attributions ORDER BY 1').fetchall()
        self.run_cli('attribution', '--days', '0')
        with closing(get_connection(self.db_path)) as conn:
            self.assertEqual(conn.execute('SELECT * FROM decision_attributions ORDER BY 1').fetchall(), before)

    def test_bad_options_are_rejected(self) -> None:
        self.run_cli('attribution', '--days', '0', '--horizon', '5')  # the options themselves are valid
        for args in (('--days', '-1'), ('--horizon', '7')):
            with self.subTest(args=args), redirect_stdout(io.StringIO()), patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    self.run_cli('attribution', *args)
                self.assertEqual(raised.exception.code, 2)


# --- Decision Autopsy API -------------------------------------------------------------------------------------------

class DecisionAutopsyAttributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = temporary_database(self)
        try:
            from api_support.decision_autopsy import build_decision_autopsy_payload
        except ModuleNotFoundError as exc:  # the API package imports Flask at module scope
            if exc.name != 'flask':
                raise
            raise unittest.SkipTest('flask is not installed in this environment') from exc
        self.payload = build_decision_autopsy_payload

    def add_decision(self, at) -> int:
        return record_manager_decision_history(
            {'symbol': 'MSFT', 'decision_run_at': at, 'manager_decision': 'call',
             'manager_input_json': {'company': {'symbol': 'MSFT'}}, 'manager_output_json': {'recommendation': {}}},
            db_path=self.db_path)

    def records(self) -> dict[int, dict]:
        return {item['manager_decision_history_id']: item for item in self.payload(limit=10)['records']}

    def test_an_attributed_decision_carries_its_labels_and_horizon_returns(self) -> None:
        decision_id = self.add_decision('2026-03-02T15:00:00+00:00')
        stored = base.record(decision_id, failure='forecast', evidence='weak', direction_outcome='wrong')
        stored['direction']['horizon_returns'] = {'5': -0.04, '10': -0.06}
        base.store().upsert_attribution(stored, db_path=self.db_path)
        self.assertEqual(self.records()[decision_id]['attribution'], {
            'evidence_quality': 'weak', 'evidence_source': 'grounding_proxy', 'direction_outcome': 'wrong',
            'expression_outcome': 'not_traded', 'primary_failure': 'forecast',
            'horizon_returns': {'5': -0.04, '10': -0.06}})

    def test_a_decision_without_an_attribution_has_null_and_the_rest_of_the_record_is_unchanged(self) -> None:
        decision_id = self.add_decision('2026-03-02T15:00:00+00:00')
        record = self.records()[decision_id]
        self.assertIn('attribution', record)
        self.assertIsNone(record['attribution'])
        self.assertEqual((record['symbol'], record['manager']['decision'] or 'call'), ('MSFT', 'call'))

    def test_each_decision_gets_only_its_own_attribution(self) -> None:
        first = self.add_decision('2026-03-02T15:00:00+00:00')
        second = self.add_decision('2026-03-03T15:00:00+00:00')
        base.store().upsert_attribution(base.record(second, failure='none'), db_path=self.db_path)
        records = self.records()
        self.assertIsNone(records[first]['attribution'])
        self.assertEqual(records[second]['attribution']['primary_failure'], 'none')


if __name__ == '__main__':
    unittest.main()
