from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import unittest

from db_common import get_connection
from market_db_modules.news_lag_report import (
    cited_article_ids, fetch_missing_history, lag_bucket, news_lag_report,
)
from tests.db_fixtures import temporary_database

NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)


def _claims(*id_lists):
    return {'strategist_recommendation': {'evidence_claims': [
        {'claim_type': 'thesis', 'claim': 'x', 'evidence_article_ids': list(ids)} for ids in id_lists]}}


class LagBucketTests(unittest.TestCase):
    def test_boundaries(self):
        cases = {59 * 60: '<1h', 3600: '1-6h', 6 * 3600 - 1: '1-6h', 6 * 3600: 'overnight',
                 24 * 3600 - 1: 'overnight', 24 * 3600: '>1d', -1: 'unknown', None: 'unknown'}
        for seconds, bucket in cases.items():
            self.assertEqual(lag_bucket(seconds), bucket, seconds)

    def test_cited_ids_union_with_or_without_payload_wrapper(self):
        self.assertEqual(cited_article_ids(json.dumps(_claims([3, 1], [1, 2]))), [1, 2, 3])
        self.assertEqual(cited_article_ids(json.dumps({'payload': _claims([5, 0, 'x'])})), [5])
        self.assertEqual(cited_article_ids(None), [])
        self.assertEqual(cited_article_ids('not json'), [])


class NewsLagReportTests(unittest.TestCase):
    def setUp(self):
        temporary_database(self)
        with closing(get_connection()) as conn, conn:
            articles = [(1, '2026-09-17T17:30:00+0000'), (2, '2026-09-17T10:00:00Z'), (3, '2026-09-20T00:00:00Z'),
                        (4, '2026-09-17T20:00:00+0000'), (5, '2026-09-11T14:00:00Z')]
            conn.executemany('''INSERT INTO news_articles (id, source, article_key, title, source_url, published_at)
                VALUES (?, 's', ?, 't', 'u', ?)''', [(i, f'k{i}', at) for i, at in articles])
            # NVDA call: newest usable citation 30m before the order, one citation dated after it.
            self._entry(conn, 'nvda', 'NVDA', 'NVDA260116C00200000', '2026-09-17T18:00:00+00:00', fill=102.0)
            self._decision(conn, 1, 'nvda', 'call', _claims([1, 2], [3]), closed='2026-09-19T15:00:00+00:00', pnl=-10.0)
            conn.execute('''INSERT INTO position_outcomes (option_symbol, entry_order_id, opened_at, closed_at,
                realized_pnl_pct, underlying_at_exit, manager_decision_history_id, primary_exit_reason)
                VALUES ('NVDA260116C00200000', 'nvda', '2026-09-17T18:00:00+00:00', '2026-09-19T15:00:00+00:00',
                40.0, 105.0, 1, 'take_profit')''')
            self._bar(conn, 'NVDA', '2026-09-17T16:00:00+00:00', '1h', 100.0)
            # AAPL put, still open: overnight lag, publish price from the daily close (20:00Z).
            self._entry(conn, 'aapl', 'AAPL', 'AAPL260116P00190000', '2026-09-18T15:00:00+00:00', decision_price=196.0)
            self._decision(conn, 2, 'aapl', 'put', {'payload': _claims([4])})
            self._bar(conn, 'AAPL', '2026-09-17T04:00:00+00:00', '1d', 200.0)
            # XOM call: >1d lag, exit underlying from the closing sell order.
            self._entry(conn, 'xom', 'XOM', 'XOM260116C00055000', '2026-09-15T14:00:00+00:00', fill=50.0)
            self._entry(conn, 'xom-sell', 'XOM', 'XOM260116C00055000', '2026-09-16T14:00:00+00:00',
                        side='sell', decision_price=55.0)
            self._decision(conn, 3, 'xom', 'call', _claims([5]), closed='2026-09-16T14:00:00+00:00', pnl=-20.0,
                           closing=['xom-sell'])
            self._bar(conn, 'XOM', '2026-09-10T04:00:00+00:00', '1d', 48.0)
            # Legacy order with the enum-style side and no decision link.
            self._entry(conn, 'legacy', 'BAC', 'BAC260116C00040000', '2026-05-11T13:32:00+00:00',
                        side='OrderSide.BUY', fill=40.0)

    @staticmethod
    def _entry(conn, order_id, symbol, option, at, *, side='buy', fill=None, decision_price=None):
        conn.execute('''INSERT INTO option_trade_executions (order_id, underlying_symbol, option_symbol, order_side,
            order_status, filled_qty, submitted_at, fill_timestamp, underlying_at_fill, underlying_at_decision)
            VALUES (?, ?, ?, ?, 'filled', 1, ?, ?, ?, ?)''', (order_id, symbol, option, side, at, at, fill, decision_price))

    @staticmethod
    def _decision(conn, decision_id, order_id, decision, manager_input, *, closed=None, pnl=None, closing=None):
        conn.execute('''INSERT INTO manager_decision_history (id, symbol, decision_run_at, manager_decision,
            trade_executed, trade_execution_order_id, closed_at, realized_pnl_pct, closing_order_ids_json)
            VALUES (?, 'X', '2026-09-01T00:00:00+00:00', ?, 1, ?, ?, ?, ?)''',
                     (decision_id, decision, order_id, closed, pnl, json.dumps(closing) if closing else None))
        conn.execute('INSERT INTO manager_decision_payloads (manager_decision_history_id, manager_input_json) VALUES (?, ?)',
                     (decision_id, json.dumps(manager_input)))

    @staticmethod
    def _bar(conn, symbol, at, timeframe, close):
        conn.execute('''INSERT INTO historical_underlying_bars (symbol, bar_at, timeframe, close_price, provider)
            VALUES (?, ?, ?, ?, 'test')''', (symbol, at, timeframe, close))

    def _trades(self, report):
        return {trade['order_id']: trade for trade in report['trades']}

    def test_call_with_hourly_baseline_and_future_citation(self):
        trade = self._trades(news_lag_report(now=NOW))['nvda']
        self.assertEqual(trade['newest_cited_published_at'], '2026-09-17T17:30:00+00:00')
        self.assertEqual((trade['lag_hours'], trade['lag_bucket']), (0.5, '<1h'))
        self.assertEqual(trade['future_dated_citations'], 1)
        self.assertEqual(trade['price_sampling'], {'publish': '1h', 'entry': 'order', 'exit': 'outcome'})
        self.assertAlmostEqual(trade['pre_entry_move_pct'], 2.0)
        self.assertAlmostEqual(trade['post_entry_move_pct'], 2.9412, places=4)
        self.assertAlmostEqual(trade['pre_entry_share'], 0.4048, places=4)
        # position_outcomes wins over manager_decision_history for P&L.
        self.assertEqual(trade['option_pnl_pct'], 40.0)
        self.assertEqual(trade['missing'], [])

    def test_put_moves_are_sign_flipped_and_open_trades_skip_pnl(self):
        report = news_lag_report(now=NOW)
        trade = self._trades(report)['aapl']
        self.assertEqual((trade['lag_hours'], trade['lag_bucket']), (19.0, 'overnight'))
        self.assertEqual(trade['price_sampling']['publish'], '1d')
        self.assertAlmostEqual(trade['pre_entry_move_pct'], 2.0)
        self.assertIsNone(trade['post_entry_move_pct'])
        self.assertIn('open_or_no_pnl', trade['missing'])
        bucket = report['buckets']['overnight']
        self.assertEqual((bucket['trades'], bucket['closed'], bucket['option_pnl_pct']['n']), (1, 0, 0))
        self.assertEqual(bucket['pre_entry_move_pct']['n'], 1)

    def test_exit_price_from_closing_order(self):
        trade = self._trades(news_lag_report(now=NOW))['xom']
        self.assertEqual(trade['lag_bucket'], '>1d')
        self.assertEqual((trade['underlying_at_exit'], trade['price_sampling']['exit']), (55.0, 'closing_order'))
        self.assertAlmostEqual(trade['post_entry_move_pct'], 10.0)
        self.assertEqual(trade['option_pnl_pct'], -20.0)

    def test_same_daily_bar_for_publish_and_entry_is_unmeasured_not_zero(self):
        with closing(get_connection()) as conn, conn:
            conn.execute('''INSERT INTO news_articles (id, source, article_key, title, source_url, published_at)
                VALUES (6, 's', 'k6', 't', 'u', '2026-09-23T13:00:00Z')''')
            # No order-time price: entry falls back to the same prior daily close as the publish time.
            self._entry(conn, 'cvx', 'CVX', 'CVX260116C00150000', '2026-09-23T15:00:00+00:00')
            self._decision(conn, 4, 'cvx', 'call', _claims([6]))
            self._bar(conn, 'CVX', '2026-09-22T04:00:00+00:00', '1d', 150.0)
        trade = self._trades(news_lag_report(now=NOW))['cvx']
        self.assertEqual(trade['lag_bucket'], '1-6h')
        self.assertIsNone(trade['pre_entry_move_pct'])
        self.assertIn('publish_and_entry_same_bar', trade['missing'])

    def test_buckets_coverage_and_legacy_side(self):
        report = news_lag_report(now=NOW)
        self.assertEqual(report['coverage']['entries'], 4)
        self.assertNotIn('xom-sell', self._trades(report))
        self.assertEqual(self._trades(report)['legacy']['lag_bucket'], 'unknown')
        self.assertEqual(report['coverage']['missing']['no_decision_link'], 1)
        self.assertEqual(report['coverage']['future_dated_citations'], 1)
        self.assertEqual(report['buckets']['<1h']['option_pnl_pct']['win_rate'], 1.0)
        self.assertEqual(report['all']['option_pnl_pct'], {'n': 2, 'mean': 10.0, 'median': 10.0, 'win_rate': 0.5, 'total': 20.0})
        json.dumps(report, allow_nan=False)

    def test_headline_and_favourable_but_lost(self):
        report = news_lag_report(now=NOW)
        trades = self._trades(report)
        # XOM rose 10% after entry but the option closed at -20%; NVDA rose and won.
        self.assertTrue(trades['xom']['favourable_but_lost'])
        self.assertFalse(trades['nvda']['favourable_but_lost'])
        self.assertFalse(trades['aapl']['favourable_but_lost'])
        self.assertEqual(report['headline'], {
            'entries': 4, 'dated_entries': 3, 'median_lag_hours': 19.0, 'within_1h': 1,
            # XOM's pre-entry move is 48 -> 50 off the daily close, so all three dated entries measure.
            'median_pre_entry_share': 0.3494, 'favourable_closed': 2, 'favourable_but_lost': 1, 'measurable': 3,
        })

    def test_days_window(self):
        self.assertEqual(sorted(self._trades(news_lag_report(days=9, now=NOW))), ['aapl', 'nvda'])
        self.assertEqual(sorted(self._trades(news_lag_report(days=10, now=NOW))), ['aapl', 'nvda', 'xom'])

    def test_fetch_missing_history_caches_hourly_bars_for_traded_symbols(self):
        calls = []

        def fetcher(symbol, timeframe):
            calls.append((symbol, timeframe))
            if timeframe == '1h' and symbol == 'AAPL':
                return 'fake', [{'bar_at': '2026-09-17T19:30:00+00:00', 'close': 201.0}]
            return 'fake', []

        result = fetch_missing_history(now=NOW, fetcher=fetcher)
        self.assertEqual(sorted({symbol for symbol, _ in calls}), ['AAPL', 'BAC', 'NVDA', 'XOM'])
        self.assertEqual(result['AAPL']['1h'], {'provider': 'fake', 'bars': 1, 'status': 'ok'})
        trade = self._trades(news_lag_report(now=NOW))['aapl']
        # The 19:30 bar completes at 20:30, after the 20:00 publish, so the daily close still applies.
        self.assertEqual(trade['price_sampling']['publish'], '1d')


if __name__ == '__main__':
    unittest.main()
