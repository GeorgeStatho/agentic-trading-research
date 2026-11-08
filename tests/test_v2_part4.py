from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import tempfile
import unittest

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database
from news_db_modules.bootstrap import initialize_database as initialize_news
from news_db_modules.entity_articles import add_company_news_article
from market_db_modules.candidate_lifecycle import record_candidate
from market_db_modules.pricing_research import (
    catalyst_stats,
    register_response_events,
    store_response,
    store_history,
)
from services.gates.option_hurdle_gate import evaluate_option_hurdle
from catalyst_models import classify_catalyst
from services.gates.market_regime_gate import evaluate_market_regime_gate, MarketRegimePolicy, apply_market_regime_gate_to_manager_result
from services.gates.pricing_policy import PricingPolicy
from services.gates.pricing_gates import apply_option_hurdle_to_manager_result

NOW = datetime(2026, 8, 10, 20, tzinfo=timezone.utc)


def histories(as_of=NOW):
    result = {role: [] for role in ('stock', 'market', 'sector')}
    prices = [100, 400, 200]
    start = as_of - timedelta(days=150)
    index = 0
    for offset in range(140):
        at = start + timedelta(days=offset)
        if at.weekday() >= 5:
            continue
        x = .006 * math.sin(index * .7) + .002 * math.sin(index * .2)
        z = .005 * math.cos(index * .31)
        for role, change, pos in zip(result, (1.2 * x + .4 * z, x, z), range(3)):
            prices[pos] *= 1 + change
            result[role].append({'bar_at': at.isoformat(), 'available_at': at.isoformat(),
                                 'timeframe': '1d', 'provider': 'fixture', 'close_price': prices[pos],
                                 'high_price': prices[pos] * 1.015, 'low_price': prices[pos] * .985})
        index += 1
    return result


def add_point(data, at, prices, timeframe='1d'):
    for role, price in zip(data, prices):
        data[role].append({'bar_at': at.isoformat(), 'available_at': at.isoformat(), 'timeframe': timeframe,
                          'close_price': price, 'high_price': price * 1.01, 'low_price': price * .99,
                          'provider': 'fixture'})


def contract(side, strike=100, expiry='2026-09-24', bid=2.9, ask=3.1, **extra):
    return {'contract_type': side, 'strike_price': strike, 'expiration_date': expiry,
            'implied_volatility': .3,
            'latest_quote': {'bid_price': bid, 'ask_price': ask, 'timestamp': NOW.isoformat()}, **extra}


class OptionHurdleTests(unittest.TestCase):
    def gate(self, contracts=None, **kwargs):
        inputs = {'spot': 100, 'stats': {'usable': True, 'expected_abs_residual_move': .03, 'n': 60},
                  'horizon_days': 5, 'as_of': NOW}
        return evaluate_option_hurdle(contracts if contracts is not None else [contract('call'), contract('put')],
                                      **{**inputs, **kwargs})

    def test_configurable_hurdle_stays_shadow(self):
        gate = self.gate(policy=replace(PricingPolicy(), move_edge_threshold=1.6))
        self.assertEqual((gate['would_allow'], gate['allowed']), (False, True))
        with self.assertRaises(ValueError):
            PricingPolicy(mode='enforce')


class RegimeScopeTests(unittest.TestCase):
    def gate(self, scope, state='range', macro=False, **kwargs):
        return evaluate_market_regime_gate(proposed_decision='call', manager_confidence='high',
            regime={'state': state, 'macro_event_window': {'active': macro}}, catalyst_scope=scope,
            policy=MarketRegimePolicy(mode='enforce'), **kwargs)

    def test_idiosyncratic_passes_range_and_systemic_blocks(self):
        self.assertTrue(self.gate('idiosyncratic')['allowed'])
        self.assertFalse(self.gate('systemic')['allowed'])
        self.assertTrue(self.gate('idiosyncratic')['scope_changed_verdict'])
        self.assertFalse(self.gate('idiosyncratic')['legacy_would_allow'])

    def test_blackout_and_unknown_regime_stay_closed_for_every_scope(self):
        for scope in ('systemic', 'sector', 'idiosyncratic', None):
            with self.subTest(scope=scope):
                self.assertFalse(self.gate(scope, macro=True)['allowed'])
                self.assertFalse(self.gate(scope, state='unknown')['allowed'])

    def test_mixed_scope_uses_strongest_regime_relevance(self):
        result = apply_market_regime_gate_to_manager_result({'recommendation': {'decision': 'call', 'confidence': 'high'},
            'market_context': {'market_regime': {'state': 'range'}},
            'candidate_context': {'catalysts': [{'catalyst_scope': 'idiosyncratic'}, {'catalyst_scope': 'systemic'}]}})
        self.assertFalse(result['market_regime_gate']['would_allow'])

    def test_scope_exception_requires_the_confidence_an_aligned_entry_needs(self):
        for state, decision in (('range', 'call'), ('stressed', 'put'), ('trend_up', 'put'), ('trend_down', 'call')):
            with self.subTest(state=state, decision=decision):
                gate = evaluate_market_regime_gate(proposed_decision=decision, manager_confidence='medium',
                    regime={'state': state}, catalyst_scope='idiosyncratic', policy=MarketRegimePolicy(mode='enforce'))
                self.assertFalse(gate['allowed'])
                self.assertIn('regime_scope_exception_requires_high_confidence', gate['reason_codes'])

    def test_unclassified_catalyst_gets_no_scope_exception(self):
        for title in ('Nvidia shares jump as investors cheer AI demand', 'Apple reportedly weighs a deal', ''):
            with self.subTest(title=title):
                self.assertEqual(classify_catalyst(title)['catalyst_scope'], 'unknown')
        self.assertEqual(classify_catalyst('Nvidia raises revenue guidance')['catalyst_scope'], 'idiosyncratic')
        # States frozen before the classifier change still carry the old lenient scope.
        frozen = {'catalyst_type': 'other', 'catalyst_scope': 'idiosyncratic'}
        result = apply_market_regime_gate_to_manager_result({'recommendation': {'decision': 'call', 'confidence': 'high'},
            'market_context': {'market_regime': {'state': 'range'}}, 'candidate_context': {'catalysts': [frozen]}})
        self.assertEqual(result['market_regime_gate']['catalyst_scope'], 'unknown')
        self.assertFalse(result['market_regime_gate']['would_allow'])

    def test_scope_does_not_bypass_high_volatility_confidence_requirement(self):
        gate = evaluate_market_regime_gate(proposed_decision='call', manager_confidence='medium',
            regime={'state': 'high_volatility'}, catalyst_scope='idiosyncratic', policy=MarketRegimePolicy(mode='enforce'))
        self.assertFalse(gate['allowed'])


class PricingDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'test.db'
        initialize_database(db_path=self.db)
        initialize_news(db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO sectors(id, sector_key, name) VALUES (1, 'technology', 'Technology')")
            conn.execute("INSERT INTO industries(id, sector_id, industry_key, name) VALUES (1, 1, 'chips', 'Chips')")
            conn.execute("INSERT INTO companies(id, industry_id, symbol, name) VALUES (1, 1, 'NVDA', 'Nvidia')")
        self.now = datetime.now(timezone.utc)
        self.article = add_company_news_article(company_id=1, source='reuters.com', article_key='one',
            title='Nvidia raises revenue guidance', source_url='https://reuters.com/one',
            published_at=(self.now - timedelta(days=3)).isoformat(), db_path=self.db)
        self.now = datetime.now(timezone.utc)
        register_response_events(as_of=self.now, db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            self.event = dict(conn.execute('SELECT * FROM catalyst_response_events').fetchone())

    def response(self, move=.03, **extra):
        return {'status': 'complete', 'outcome_at': self.now.isoformat(), 'residual_return': move,
                'mfe': .04, 'mae': -.01, **extra}

    def stats(self, **extra):
        return catalyst_stats('guidance_raise', 5, as_of=self.now, minimum_samples=2, db_path=self.db, **extra)

    def test_completed_responses_are_immutable_and_deduplicated(self):
        store_response(self.event, 5, self.response(), as_of=self.now, db_path=self.db)
        store_response(self.event, 5, self.response(.9), as_of=self.now, db_path=self.db)
        self.assertEqual((self.stats()['n'], self.stats()['median_residual_return']), (1, .03))
        self.assertFalse(self.stats()['usable'])

    def store_fixture_history(self, data):
        for role, symbol in (('stock', 'NVDA'), ('market', 'SPY'), ('sector', 'XLK')):
            for timeframe in ('1d', '1h'):
                bars = [{'bar_at': bar['bar_at'], 'available_at': bar['available_at'], 'close': bar['close_price'],
                         'high': bar['high_price'], 'low': bar['low_price']}
                        for bar in data[role] if bar['timeframe'] == timeframe]
                store_history(symbol, bars, provider='fixture', timeframe=timeframe,
                              captured_at=self.now, db_path=self.db)

    def test_future_outcome_and_future_capture_are_excluded(self):
        for field in ('outcome', 'recorded'):
            with self.subTest(field=field):
                with closing(get_connection(self.db)) as conn, conn:
                    conn.execute('DELETE FROM catalyst_responses')
                later = self.now + timedelta(days=1)
                response = self.response(outcome_at=(later if field == 'outcome' else self.now).isoformat())
                store_response(self.event, 5, response, as_of=later if field == 'recorded' else self.now, db_path=self.db)
                self.assertEqual(self.stats()['n'], 0)

    def candidate(self):
        return record_candidate('NVDA', {'pipeline_version': 'fixture-v4'}, db_path=self.db)

    def test_option_gate_persists_before_contract_selection_without_changing_recommendation(self):
        context = self.candidate()
        result = {'candidate_context': context, 'recommendation': {'decision': 'call', 'confidence': 'high'},
                  'market_context': {'current_stock_price': {'price': 100},
                                     'option_market': {'contracts': [contract('call'), contract('put')]}}}
        output = apply_option_hurdle_to_manager_result(result, as_of=NOW, db_path=self.db)
        self.assertEqual(output['recommendation'], result['recommendation'])
        self.assertIsNone(output['option_hurdle_gate']['would_allow'])
        with closing(get_connection(self.db)) as conn, conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM candidate_snapshots WHERE stage='OPTION_HURDLE'").fetchone()[0], 1)

    def test_directed_absorption_signs_only_undirected_verdicts_and_persists_a_separate_stage(self):
        from services.research.price_response import directed_absorption
        from services.gates.pricing_gates import record_directed_absorption
        undirected = {'stage': 'PRICE_ABSORPTION', 'allowed': True, 'would_allow': None, 'enforcement_mode': 'shadow',
                      'reason_codes': ['price_response_unknown'], 'thresholds': {'absorption_ratio': 1.25},
                      'metrics': {'price_absorption_ratio': 1.5, 'residual_move_since_event': .04}}
        self.assertFalse(directed_absorption(undirected, 'call')['would_allow'])
        self.assertTrue(directed_absorption(undirected, 'put')['would_allow'])
        self.assertIsNone(directed_absorption(undirected, 'neither'))
        self.assertIsNone(directed_absorption({**undirected, 'would_allow': True}, 'call'))
        self.assertIsNone(directed_absorption({**undirected, 'metrics': {'price_absorption_ratio': None}}, 'call'))
        self.assertIsNone(directed_absorption(None, 'call'))
        context = {**self.candidate(), 'price_absorption_gate': undirected}
        result = {'candidate_context': context, 'recommendation': {'decision': 'call'}}
        gate = record_directed_absorption(result, as_of=NOW, db_path=self.db)
        self.assertEqual((gate['stage'], gate['allowed'], gate['reason_codes']), ('PRICE_ABSORPTION_DIRECTED', True, ['already_priced']))
        self.assertEqual(result['recommendation'], {'decision': 'call'})
        with closing(get_connection(self.db)) as conn, conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM candidate_snapshots WHERE stage='PRICE_ABSORPTION_DIRECTED'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT shadow FROM candidate_rejections WHERE stage='PRICE_ABSORPTION_DIRECTED'").fetchone()[0], 1)

    def test_fresh_news_fetches_completed_hourly_bars_once_and_completes_the_verdict(self):
        from market_db_modules.pricing_research import candidate_sector_symbol
        from services.gates import pricing_gates
        pricing_gates._INTRADAY_FETCHED.clear()
        data = histories(as_of=NOW + timedelta(days=9))  # daily bars through the Friday before NOW (a Monday)
        for role in data:
            data[role] = [bar for bar in data[role] if bar['timeframe'] == '1d']
        self.now = NOW - timedelta(days=1)  # captured before the candidate, or the gate may not read it
        self.store_fixture_history(data)
        event = NOW - timedelta(hours=3)
        prices = {'NVDA': (101, 104), 'SPY': (400, 400), candidate_sector_symbol('NVDA', db_path=self.db): (200, 200)}
        calls = []

        def fetcher(symbol, timeframe):
            calls.append((symbol, timeframe))
            before, after = prices[symbol]
            starts = (event - timedelta(hours=1, minutes=30), event + timedelta(minutes=30), NOW)  # the last one is unfinished
            return 'fixture', [{'bar_at': start.isoformat(), 'close': close, 'high': close, 'low': close}
                               for start, close in zip(starts, (before, after, after))]

        state = {'catalyst_id': 'catalyst:fresh', 'catalyst_family': 'guidance_raise', 'expected_horizon': '1_3_days',
                 'first_public_time': event.isoformat(), 'direction': 'positive'}
        context = {**self.candidate(), 'catalysts': [state], 'created_at': NOW.isoformat()}
        gate = pricing_gates.record_candidate_price_response(context, fetcher=fetcher, db_path=self.db)
        self.assertEqual(sorted(symbol for symbol, _ in calls), sorted(prices))
        self.assertEqual({timeframe for _, timeframe in calls}, {'1h'})
        self.assertEqual(gate['metrics']['status'], 'complete')
        self.assertGreater(gate['metrics']['residual_move_since_event'], 0)
        self.assertLessEqual(datetime.fromisoformat(gate['metrics']['outcome_at']), NOW)
        self.assertIn('NVDA', gate['metrics']['intraday_refresh'])
        self.assertEqual(pricing_gates.refresh_intraday_history(list(prices), as_of=NOW + timedelta(minutes=5),
                                                                fetcher=fetcher, db_path=self.db), {})
        # An earlier candidate cannot see bars stamped at NOW, so it fetches again and the bars keep the earliest stamp.
        earlier = NOW - timedelta(minutes=1)
        self.assertEqual(set(pricing_gates.refresh_intraday_history(['NVDA'], as_of=earlier, fetcher=fetcher, db_path=self.db)), {'NVDA'})
        store_history('NVDA', [{'bar_at': (event + timedelta(minutes=30)).isoformat(), 'close': 1}], provider='fixture',
                      timeframe='1h', captured_at=NOW + timedelta(days=1), db_path=self.db)
        with closing(get_connection(self.db)) as conn:
            stamps = conn.execute("SELECT DISTINCT created_at FROM historical_underlying_bars WHERE symbol='NVDA' AND timeframe='1h'").fetchall()
        self.assertEqual([row[0] for row in stamps], [earlier.isoformat()])

    def test_forward_return_of_an_undirected_candidate_keeps_only_the_raw_residual(self):
        import json
        from services.discovery.catalyst_response_collector import capture_forward_returns
        data = histories()
        self.store_fixture_history(data)
        observed = NOW - timedelta(days=60)
        baseline = [bar['close_price'] for bar in (max((b for b in data[role] if datetime.fromisoformat(b['bar_at']) <= observed),
                                                      key=lambda b: b['bar_at']) for role in ('stock', 'market', 'sector'))]
        context = self.candidate()
        payload = {'ticker': 'NVDA', 'sector_symbol': 'XLK', 'direction': 'unknown',
                   'gate': {'metrics': {'status': 'complete', 'betas': {'beta_market': 1.0, 'beta_sector': 0.0},
                                        'endpoint_prices': baseline}}}
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('INSERT INTO candidate_price_observations (candidate_id, catalyst_id, observed_at, bucket, snapshot_json) VALUES (?,?,?,?,?)',
                         (context['candidate_id'], context.get('catalyst_id'), observed.isoformat(), 'partially_expressed', json.dumps(payload)))
        capture_forward_returns(as_of=NOW, db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            signed, raw = conn.execute('SELECT forward_residual_return, forward_raw_residual_return FROM candidate_price_observations').fetchone()
        self.assertIsNone(signed)
        self.assertIsNotNone(raw)

    def test_forward_return_is_measured_for_a_candidate_whose_gate_metrics_were_incomplete(self):
        import json
        from services.discovery.catalyst_response_collector import capture_forward_returns
        self.store_fixture_history(histories())
        context = self.candidate()
        payload = {'ticker': 'NVDA', 'sector_symbol': 'XLK', 'direction': 'positive',
                   'gate': {'metrics': {'status': 'missing_event_time'}}}
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('INSERT INTO candidate_price_observations (candidate_id, catalyst_id, observed_at, bucket, snapshot_json) VALUES (?,?,?,?,?)',
                         (context['candidate_id'], context.get('catalyst_id'), (NOW - timedelta(days=60)).isoformat(),
                          'unknown', json.dumps(payload)))
        capture_forward_returns(as_of=NOW, db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            signed, raw, at = conn.execute('SELECT forward_residual_return, forward_raw_residual_return, forward_at '
                                           'FROM candidate_price_observations').fetchone()
        self.assertIsNotNone(at)
        self.assertAlmostEqual(signed, raw)

    def test_migration_is_repeatable_and_preserves_existing_articles(self):
        initialize_database(db_path=self.db)
        initialize_database(db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM news_articles').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
