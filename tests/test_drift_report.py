"""The drift report: residual from first observation, one row per catalyst, by family, direction and age."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest import mock

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


class CatalystFeedReportTests(unittest.TestCase):
    """The shares_drift rule replayed on catalysts as they were labelled, one open position per ticker."""

    def setUp(self) -> None:
        self.db = temporary_database(self)
        self.calls, self.outcomes = [], {}

    def catalyst(self, catalyst, *, ticker='MSFT', seen_days_ago=10, published_hours_before=1.0,
                 family='earnings_positive', direction='positive', source='rules+model', labels=(),
                 candidate_hours_after=None):
        """A catalyst first seen `seen_days_ago`; `labels` are (hours after first seen, direction, confidence)."""
        seen = NOW - timedelta(days=seen_days_ago)
        published = seen - timedelta(hours=published_hours_before)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('''INSERT INTO catalysts (catalyst_id, ticker, catalyst_type, catalyst_subtype,
                catalyst_family, catalyst_scope, direction, mechanism, expected_horizon, first_public_time,
                first_seen_time, article_count, source_count, independent_root_count, primary_source_present,
                classification_version, classification_status, state_json, updated_at)
                VALUES (?, ?, 'earnings', 'x', ?, 'idiosyncratic', ?, 'm', '4_10_days', ?, ?, 1, 1, 1, 0, 'v',
                'classified', ?, ?)''',
                         (catalyst, ticker, family, direction, published.isoformat(), seen.isoformat(),
                          json.dumps({'classification_source': source}), NOW.isoformat()))
            for hours, way, confidence in labels:
                conn.execute('''INSERT INTO catalyst_model_labels (catalyst_id, label_version, catalyst_type,
                    direction, confidence, model, answered_by_json, labeled_at) VALUES (?, 'v1', 'earnings', ?, ?,
                    'm', '{}', ?)''', (catalyst, way, confidence, (seen + timedelta(hours=hours)).isoformat()))
            if candidate_hours_after is not None:
                candidate = f'candidate-{catalyst}'
                conn.execute("INSERT INTO candidates (candidate_id, symbol, created_at, catalyst_ids_json, "
                             "article_ids_json, pipeline_version_json) VALUES (?, ?, ?, '[]', '[]', '{}')",
                             (candidate, ticker, (seen + timedelta(hours=candidate_hours_after)).isoformat()))
                conn.execute('INSERT INTO candidate_catalysts VALUES (?, ?, ?, 1, ?)',
                             (candidate, catalyst, family, '{}'))
        return seen

    def price(self, conn, inputs, decision, run, as_of, reads, db_path=None):
        """A stand-in for stock_position: entry at the cutoff, exit five days later, outcomes set per symbol."""
        self.calls.append((inputs['symbol'], inputs['information_cutoff_time'], decision))
        entry = datetime.fromisoformat(inputs['information_cutoff_time'])
        status, raw, residual = self.outcomes.get(inputs['symbol'], ('complete', .02, .01))
        if status != 'complete':
            return {'status': status}
        return {'status': 'complete', 'capital': 1000, 'hypothetical_pnl': raw * 1000,
                'residual_pnl': None if residual is None else residual * 1000,
                'residual_status': 'complete' if residual is not None else 'window_mismatch',
                'hypothetical_entry': {'at': entry.isoformat()},
                'hypothetical_exit': {'at': (entry + timedelta(days=5)).isoformat()}}

    def report(self, **kwargs):
        from market_db_modules.drift_report import catalyst_feed_report
        return catalyst_feed_report(db_path=self.db, now=NOW, price=self.price, **kwargs)

    def test_a_publication_older_than_a_day_at_first_sight_is_never_traded(self):
        self.catalyst('catalyst:late', source='rules', published_hours_before=9 * 24)
        result = self.report()
        self.assertEqual(self.calls, [])
        self.assertEqual(result['point_in_time']['coverage']['stale_publication'], 1)

    def test_rules_enter_at_first_seen_and_a_model_direction_at_its_first_usable_matching_label(self):
        rules = self.catalyst('catalyst:rules', ticker='AAA', source='rules')
        model = self.catalyst('catalyst:model', ticker='BBB', labels=[(0.5, 'positive', 'low'),
                                                                     (1.0, 'negative', 'high'),
                                                                     (2.0, 'positive', 'medium'),
                                                                     (3.0, 'positive', 'high')])
        self.report()
        self.assertEqual(sorted(self.calls), [('AAA', rules.isoformat(), 'call'),
                                              ('BBB', (model + timedelta(hours=2)).isoformat(), 'call')])

    def test_a_late_or_missing_label_is_out_of_point_in_time_and_in_the_assumed_lag_section(self):
        late = self.catalyst('catalyst:late-label', ticker='AAA', labels=[(30, 'positive', 'high')])
        self.catalyst('catalyst:no-label', ticker='BBB', seen_days_ago=20)
        result = self.report()
        self.assertEqual(result['point_in_time']['overall']['catalysts'], 0)
        self.assertEqual(result['point_in_time']['coverage']['no_in_time_label'], 2)
        self.assertEqual(result['assumed_label_lag']['overall']['catalysts'], 2)
        self.assertIn(('AAA', (late + timedelta(hours=2)).isoformat(), 'call'), self.calls)

    def test_a_ticker_with_an_open_position_skips_later_signals_whatever_their_direction(self):
        self.catalyst('catalyst:first', source='rules', seen_days_ago=12)
        self.catalyst('catalyst:opposite', source='rules', seen_days_ago=11, family='earnings_negative',
                      direction='negative')
        self.catalyst('catalyst:after-exit', source='rules', seen_days_ago=6)
        result = self.report()['point_in_time']
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(result['coverage']['ticker_open'], 1)
        self.assertEqual(result['overall']['catalysts'], 2)

    def test_negative_is_a_put_and_rows_split_by_family_and_by_candidate(self):
        self.catalyst('catalyst:pos', ticker='AAA', source='rules', candidate_hours_after=3)
        self.catalyst('catalyst:neg', ticker='BBB', source='rules', family='earnings_negative', direction='negative',
                      candidate_hours_after=48)
        self.outcomes['BBB'] = ('complete', -.04, -.02)
        result = self.report()['point_in_time']
        self.assertIn(('BBB', mock.ANY, 'put'), self.calls)
        groups = {(row['family'], row['direction']): row['mean_raw_return'] for row in result['groups']}
        self.assertAlmostEqual(groups[('earnings_negative', 'negative')], -.04)
        by_candidate = {row['had_candidate']: row['catalysts'] for row in result['by_candidate']}
        self.assertEqual(by_candidate, {True: 1, False: 1})  # a candidate two days later is not today's feed
        self.assertAlmostEqual(result['overall']['mean_raw_return'], -.01)

    def test_pending_and_missing_prices_are_counted_and_kept_out_of_the_stats(self):
        self.catalyst('catalyst:priced', ticker='AAA', source='rules')
        self.catalyst('catalyst:pending', ticker='BBB', source='rules', seen_days_ago=1)
        self.catalyst('catalyst:missing', ticker='CCC', source='rules')
        self.catalyst('catalyst:unmeasured', ticker='DDD', source='rules')
        self.outcomes.update({'BBB': ('pending', None, None), 'CCC': ('missing_stock_prices', None, None),
                              'DDD': ('complete', .05, None)})
        result = self.report()['point_in_time']
        self.assertEqual((result['coverage']['pending'], result['coverage']['missing_stock_prices'],
                          result['coverage']['window_mismatch']), (1, 1, 1))
        self.assertEqual((result['overall']['catalysts'], result['overall']['residual_catalysts']), (2, 1))

    def test_days_limits_signals_by_first_seen(self):
        self.catalyst('catalyst:old', ticker='AAA', source='rules', seen_days_ago=40)
        self.catalyst('catalyst:new', ticker='BBB', source='rules')
        self.assertEqual(self.report(days=30)['point_in_time']['overall']['catalysts'], 1)


if __name__ == '__main__':
    unittest.main()
