"""The drift report: residual from first observation, one row per catalyst, by family, direction and age."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import unittest

from db_common import get_connection
from market_db_modules.drift_report import age_bucket, drift_report
from tests.db_fixtures import temporary_database

NOW = datetime(2026, 11, 16, 15, tzinfo=timezone.utc)


class DriftReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = temporary_database(self)
        self.count = 0

    def observe(self, catalyst, *, family='earnings_positive', direction='positive', raw=.01,
                age_hours=2.0, days_ago=7, current_direction=None, current_family=None):
        """One candidate observation of `catalyst`, first seen `age_hours` before it was observed."""
        self.count += 1
        observed = NOW - timedelta(days=days_ago)
        first_seen = observed - timedelta(hours=age_hours) if age_hours is not None else None
        state = {'catalyst_id': catalyst, 'catalyst_family': family, 'direction': direction,
                 'first_seen_time': first_seen.isoformat() if first_seen else None}
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('''INSERT OR IGNORE INTO catalysts (catalyst_id, ticker, catalyst_type, catalyst_subtype,
                catalyst_family, catalyst_scope, direction, mechanism, expected_horizon, article_count, source_count,
                independent_root_count, primary_source_present, classification_version, classification_status,
                state_json, updated_at) VALUES (?, 'MSFT', 'earnings', 'x', ?, 'idiosyncratic', ?, 'm', '4_10_days',
                1, 1, 1, 0, 'v', 'classified', '{}', ?)''',
                         (catalyst, current_family or family, current_direction or direction, NOW.isoformat()))
            candidate = f'candidate-{self.count}'
            conn.execute('INSERT INTO candidate_catalysts VALUES (?, ?, ?, 1, ?)',
                         (candidate, catalyst, family, json.dumps(state)))
            conn.execute('INSERT INTO candidate_price_observations (candidate_id, catalyst_id, observed_at, bucket, '
                         'snapshot_json, forward_raw_residual_return, forward_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                         (candidate, catalyst, observed.isoformat(), 'b', json.dumps({'direction': direction}),
                          raw, (observed + timedelta(days=5)).isoformat() if raw is not None else None))

    def report(self, **kwargs):
        return drift_report(db_path=self.db, now=NOW, **kwargs)

    def group(self, result, family, direction, age, key='groups'):
        return next(row for row in result[key] if (row['family'], row['direction'], row['age']) == (family, direction, age))

    def test_age_buckets_have_exact_edges(self):
        self.assertEqual([age_bucket(h) for h in (0, .999, 1, 23.999, 24, None, -1)],
                         ['<1h', '<1h', '1-24h', '1-24h', '>24h', 'unknown', 'unknown'])

    def test_each_catalyst_counts_once_at_its_first_observation(self):
        self.observe('catalyst:a', raw=.02, age_hours=2, days_ago=8)
        self.observe('catalyst:a', raw=-.05, age_hours=50, days_ago=6)
        row = self.group(self.report(), 'earnings_positive', 'positive', '1-24h')
        self.assertEqual((row['catalysts'], row['mean_signed_residual'], row['hit_rate']), (1, .02, 1.0))
        self.assertFalse(any(row['age'] == '>24h' for row in self.report()['groups']))

    def test_negative_direction_is_signed_and_unknown_direction_is_never_signed(self):
        self.observe('catalyst:n', family='earnings_negative', direction='negative', raw=-.03)
        self.observe('catalyst:u', family='analyst', direction='unknown', raw=.04)
        result = self.report()
        self.assertEqual(self.group(result, 'earnings_negative', 'negative', '1-24h')['mean_signed_residual'], .03)
        unknown = self.group(result, 'analyst', 'unknown', '1-24h')
        self.assertEqual((unknown['mean_signed_residual'], unknown['hit_rate'], unknown['mean_raw_residual']),
                         (None, None, .04))
        self.assertEqual(result['overall']['catalysts'], 1)

    def test_distinct_days_and_the_overall_line(self):
        self.observe('catalyst:1', raw=.02, days_ago=7)
        self.observe('catalyst:2', raw=-.01, days_ago=7)
        self.observe('catalyst:3', raw=.01, days_ago=9)
        overall = self.report()['overall']
        self.assertEqual((overall['catalysts'], overall['days']), (3, 2))
        self.assertAlmostEqual(overall['mean_signed_residual'], .02 / 3)
        self.assertAlmostEqual(overall['hit_rate'], 2 / 3)

    def test_days_filter_and_unlabelled_rows(self):
        self.observe('catalyst:old', days_ago=40)
        self.observe('catalyst:new', days_ago=7)
        self.observe('catalyst:pending', raw=None, days_ago=7)
        result = self.report(days=30)
        self.assertEqual(result['overall']['catalysts'], 1)
        self.assertEqual(result['coverage']['unlabelled_catalysts'], 1)

    def test_later_labels_are_reported_apart(self):
        self.observe('catalyst:late', family='other', direction='unknown', current_family='analyst',
                     current_direction='positive', raw=.02)
        result = self.report()
        self.assertEqual(result['overall']['catalysts'], 0)
        later = self.group(result, 'analyst', 'positive', '1-24h', key='uses_later_labels')
        self.assertEqual(later['mean_signed_residual'], .02)

    def test_the_all_observations_line_averages_each_catalyst_over_every_directed_observation(self):
        self.observe('catalyst:a', direction='unknown', raw=.05, days_ago=9)
        self.observe('catalyst:a', raw=.02, days_ago=8)
        self.observe('catalyst:a', raw=-.04, days_ago=7)
        self.observe('catalyst:b', direction='negative', family='earnings_negative', raw=-.03, days_ago=7)
        result = self.report()
        self.assertEqual(result['overall']['catalysts'], 1)
        every = result['overall_all_observations']
        self.assertEqual((every['catalysts'], every['days']), (2, 2))
        self.assertAlmostEqual(every['mean_signed_residual'], (-.01 + .03) / 2)
        self.assertEqual(every['hit_rate'], .5)

    def test_missing_first_seen_time_is_unknown_age(self):
        self.observe('catalyst:x', age_hours=None)
        self.assertEqual(self.group(self.report(), 'earnings_positive', 'positive', 'unknown')['catalysts'], 1)


class SharesDriftReportTests(unittest.TestCase):
    def setUp(self) -> None:
        from market_db_modules.research_runs import start_run
        self.db = temporary_database(self)
        self.run_id = start_run({'pipeline_version': 'fixture'}, kind='leakage_checked_backtest', db_path=self.db,
                                now=NOW.isoformat())['run_id']
        self.count = 0

    def trade(self, catalyst, *, raw, residual, days_ago=7, hours=0, status='complete', family='earnings_positive',
              direction='positive'):
        self.count += 1
        candidate, cutoff = f'candidate-{self.count}', (NOW - timedelta(days=days_ago, hours=hours)).isoformat()
        result = {'status': status, 'hypothetical_pnl': None if raw is None else raw * 1000, 'capital': 1000,
                  'residual_pnl': None if residual is None else residual * 1000}
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO candidates (candidate_id, symbol, created_at, catalyst_ids_json, article_ids_json, "
                         "pipeline_version_json) VALUES (?, 'MSFT', ?, '[]', '[]', '{}')", (candidate, cutoff))
            conn.execute('''INSERT OR IGNORE INTO catalysts (catalyst_id, ticker, catalyst_type, catalyst_subtype,
                catalyst_family, catalyst_scope, direction, mechanism, expected_horizon, article_count, source_count,
                independent_root_count, primary_source_present, classification_version, classification_status,
                state_json, updated_at) VALUES (?, 'MSFT', 'earnings', 'x', ?, 'idiosyncratic', ?, 'm', '4_10_days',
                1, 1, 1, 0, 'v', 'classified', '{}', ?)''', (catalyst, family, direction, cutoff))
            conn.execute('INSERT INTO research_candidates VALUES (?, ?, ?, ?, ?)', (self.run_id, candidate, cutoff, 'h', '{}'))
            conn.execute('INSERT INTO research_shadow_positions VALUES (?, ?, ?, ?, ?, ?, ?)',
                         (self.run_id, candidate, 'shares_drift', cutoff, NOW.isoformat(), status, json.dumps(result)))
            conn.execute('INSERT INTO candidate_catalysts VALUES (?, ?, ?, 1, ?)', (candidate, catalyst, family,
                         json.dumps({'catalyst_family': family, 'direction': direction})))

    def report(self):
        from market_db_modules.drift_report import shares_drift_report
        return shares_drift_report(self.run_id, db_path=self.db)

    def test_each_catalyst_counts_once_at_its_first_entry_and_residual_is_reported(self):
        self.trade('catalyst:a', raw=.03, residual=.01, hours=5)
        self.trade('catalyst:a', raw=-.08, residual=-.06, hours=1)  # a repeat candidate on the same catalyst
        self.trade('catalyst:b', raw=-.01, residual=.02, days_ago=6)
        self.trade('catalyst:c', raw=None, residual=None, status='cash')
        result = self.report()
        overall = result['overall']
        self.assertEqual((result['trades'], overall['catalysts'], overall['days']), (3, 2, 2))
        self.assertAlmostEqual(overall['mean_raw_return'], .01)
        self.assertAlmostEqual(overall['mean_residual_return'], .015)
        self.assertEqual(overall['residual_hit_rate'], 1.0)

    def test_an_unmeasured_residual_is_counted_apart(self):
        self.trade('catalyst:a', raw=.03, residual=None)
        overall = self.report()['overall']
        self.assertEqual((overall['catalysts'], overall['residual_catalysts'], overall['mean_residual_return']), (1, 0, None))

    def test_groups_by_family_and_direction(self):
        self.trade('catalyst:a', raw=.03, residual=.01)
        self.trade('catalyst:n', raw=.02, residual=.04, family='earnings_negative', direction='negative')
        groups = {(row['family'], row['direction']): row['catalysts'] for row in self.report()['groups']}
        self.assertEqual(groups, {('earnings_positive', 'positive'): 1, ('earnings_negative', 'negative'): 1})


if __name__ == '__main__':
    unittest.main()
