from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
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
    catalyst_stats, register_response_events, store_response, store_history,
    pricing_report, record_price_observation, load_histories,
)
from services.price_response import fit_betas, measure_response, evaluate_price_absorption, completed_at
from services.option_hurdle_gate import evaluate_option_hurdle, select_atm_research_contracts
from catalyst_models import classify_catalyst
from services.market_regime_gate import evaluate_market_regime_gate, MarketRegimePolicy, apply_market_regime_gate_to_manager_result
from services.pricing_policy import PricingPolicy
from services.pricing_gates import record_candidate_price_response, apply_option_hurdle_to_manager_result
from services.catalyst_response_collector import collect_catalyst_responses, refresh_universe_history, capture_forward_returns

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


class PriceResponseTests(unittest.TestCase):
    def setUp(self):
        self.data = histories()
        self.event = datetime.fromisoformat(self.data['stock'][-1]['available_at']) + timedelta(hours=1)
        self.state = {'first_public_time': self.event.isoformat(), 'direction': 'positive'}

    def test_joint_regression_recovers_market_and_sector_betas(self):
        betas = fit_betas(self.data, before=self.event)
        self.assertAlmostEqual(betas['beta_market'], 1.2, places=10)
        self.assertAlmostEqual(betas['beta_sector'], .4, places=10)
        self.assertEqual(betas['n'], 60)

    def test_future_event_jump_cannot_change_fitted_betas(self):
        expected = fit_betas(self.data, before=self.event)
        add_point(self.data, self.event + timedelta(days=1), [10000, 1, 2000])
        self.assertEqual(fit_betas(self.data, before=self.event), expected)

    def test_singular_market_sector_history_stays_unknown(self):
        self.data['sector'] = self.data['market']
        self.assertIsNone(fit_betas(self.data, before=self.event))

    def test_missing_sector_never_becomes_zero_beta(self):
        self.data['sector'] = []
        gate = evaluate_price_absorption(self.state, self.data, {}, as_of=NOW)
        self.assertIsNone(gate['metrics']['residual_move_since_event'])
        self.assertIsNone(gate['would_allow'])
        self.assertTrue(gate['allowed'])

    def test_daily_bar_not_available_at_its_midnight_label(self):
        bar = {'bar_at': '2026-08-10T00:00:00Z', 'timeframe': '1d'}
        self.assertEqual(completed_at(bar), NOW)

    def test_one_hour_outcome_never_uses_daily_close(self):
        prices = [self.data[role][-1]['close_price'] for role in self.data]
        add_point(self.data, self.event + timedelta(days=1), prices)
        response = measure_response(self.data, event_at=self.event.isoformat(), as_of=NOW, horizon_days=1 / 24)
        self.assertEqual(response['status'], 'missing_aligned_prices')

    def test_hourly_endpoint_and_directional_excursions(self):
        prices = [self.data[role][-1]['close_price'] for role in self.data]
        add_point(self.data, self.event, prices, '1h')
        add_point(self.data, self.event + timedelta(hours=1), [prices[0] * .96, *prices[1:]], '1h')
        response = measure_response(self.data, event_at=self.event.isoformat(), as_of=NOW,
                                    horizon_days=1 / 24, direction='negative')
        self.assertAlmostEqual(response['residual_return'], -.04)
        self.assertAlmostEqual(response['mfe'], .04)
        self.assertEqual(response['mae'], 0)

    def test_absorption_bands_direction_and_shadow_verdict(self):
        prices = [self.data[role][-1]['close_price'] for role in self.data]
        stats = {'usable': True, 'expected_abs_residual_move': .02, 'n': 40}
        for move, bucket, allow in ((.005, 'largely_unexpressed', True), (.01, 'partially_expressed', True),
                                    (.02, 'substantially_expressed', True), (.03, 'potentially_overabsorbed', False),
                                    (-.03, 'potentially_overabsorbed', True)):
            with self.subTest(move=move):
                data = {role: list(bars) for role, bars in self.data.items()}
                add_point(data, self.event + timedelta(days=1), [prices[0] * (1 + move), *prices[1:]])
                gate = evaluate_price_absorption(self.state, data, stats, as_of=self.event + timedelta(days=1))
                self.assertEqual((gate['metrics']['bucket'], gate['would_allow'], gate['allowed']), (bucket, allow, True))

    def test_insufficient_empirical_sample_uses_provisional_atr(self):
        prices = [self.data[role][-1]['close_price'] for role in self.data]
        add_point(self.data, self.event + timedelta(days=1), [prices[0] * 1.04, *prices[1:]])
        gate = evaluate_price_absorption(self.state, self.data,
                                         {'usable': False, 'expected_abs_residual_move': .001, 'n': 1},
                                         as_of=self.event + timedelta(days=1))
        self.assertEqual(gate['metrics']['normalization'], 'atr_14')
        self.assertEqual(gate['metrics']['calibration'], 'provisional')
        self.assertIsNotNone(gate['metrics']['price_absorption_ratio'])

    def test_unmatured_response_cannot_be_completed(self):
        response = measure_response(self.data, event_at=NOW.isoformat(), as_of=NOW, horizon_days=5)
        self.assertEqual(response['status'], 'pending_horizon')


class OptionHurdleTests(unittest.TestCase):
    def test_research_keeps_atm_pair_from_chain_before_otm_selection(self):
        contracts = [contract('call', 110), contract('put', 90), contract('call'), contract('put')]
        selected = select_atm_research_contracts(contracts, 100)
        self.assertEqual([(row['contract_type'], row['strike_price']) for row in selected], [('call', 100), ('put', 100)])

    def gate(self, contracts=None, **kwargs):
        inputs = {'spot': 100, 'stats': {'usable': True, 'expected_abs_residual_move': .03, 'n': 60},
                  'horizon_days': 5, 'as_of': NOW}
        return evaluate_option_hurdle(contracts if contracts is not None else [contract('call'), contract('put')],
                                      **{**inputs, **kwargs})

    def test_straddle_and_iv_use_distinct_horizon_scaled_units(self):
        gate = self.gate()
        row = gate['metrics']['expirations'][0]
        self.assertAlmostEqual(row['straddle_implied_move'], .06)
        self.assertAlmostEqual(row['implied_move_over_horizon'], .02)
        self.assertAlmostEqual(row['iv_implied_move_1sigma'], .3 * math.sqrt(5 / 365))
        self.assertAlmostEqual(row['move_edge_ratio'], 1.5)
        self.assertEqual(row['sample_size'], 60)

    def test_configurable_hurdle_stays_shadow(self):
        gate = self.gate(policy=replace(PricingPolicy(), move_edge_threshold=1.6))
        self.assertEqual((gate['would_allow'], gate['allowed']), (False, True))
        with self.assertRaises(ValueError):
            PricingPolicy(mode='enforce')

    def test_single_otm_premium_is_not_an_implied_move(self):
        gate = self.gate([contract('call', strike=110, bid=.1, ask=.2)])
        self.assertIsNone(gate['metrics']['expirations'][0]['straddle_implied_move'])
        self.assertIsNone(gate['would_allow'])

    def test_cross_expiry_or_strike_pairs_are_not_straddles(self):
        for put in (contract('put', strike=101), contract('put', expiry='2026-09-25')):
            with self.subTest(put=put):
                self.assertIsNone(self.gate([contract('call'), put])['would_allow'])

    def test_bad_and_stale_quotes_do_not_create_edge(self):
        for quote in ({'bid_price': 4, 'ask_price': 3, 'timestamp': NOW.isoformat()},
                      {'bid_price': 0, 'ask_price': 3, 'timestamp': NOW.isoformat()},
                      {'bid_price': float('nan'), 'ask_price': 3, 'timestamp': NOW.isoformat()},
                      {'bid_price': 2, 'ask_price': 3, 'timestamp': (NOW - timedelta(hours=1)).isoformat()},
                      {'bid_price': 2, 'ask_price': 3, 'timestamp': (NOW + timedelta(seconds=1)).isoformat()},
                      {'bid_price': 2, 'ask_price': 3}):
            with self.subTest(quote=quote):
                self.assertIsNone(self.gate([contract('call', latest_quote=quote), contract('put')])['would_allow'])

    def test_missing_atm_quote_cannot_fall_back_to_far_strike(self):
        contracts = [contract('call'), contract('call', 110), contract('put', 110)]
        self.assertIsNone(self.gate(contracts)['would_allow'])

    def test_small_sample_retains_implied_move_but_no_verdict(self):
        gate = self.gate(stats={'usable': False, 'expected_abs_residual_move': .03, 'n': 2})
        row = gate['metrics']['expirations'][0]
        self.assertIsNone(row['move_edge_ratio'])
        self.assertEqual(row['straddle_implied_move'], .06)
        self.assertIsNone(gate['would_allow'])

    def test_contract_expiring_before_holding_horizon_is_unknown(self):
        self.assertIsNone(self.gate(horizon_days=60)['would_allow'])


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

    def test_penalty_severity_and_countertrend(self):
        self.assertAlmostEqual(self.gate('sector', state='stressed')['regime_opposition_penalty'], 1.2)
        self.assertFalse(self.gate('sector', state='stressed')['allowed'])
        self.assertTrue(self.gate('idiosyncratic', state='trend_down')['allowed'])
        self.assertFalse(self.gate(None)['allowed'])

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

    def test_registration_does_not_depend_on_candidates_or_trades(self):
        register_response_events(as_of=self.now, db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            counts = [conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                      for table in ('catalyst_response_events', 'candidates', 'option_trade_executions')]
        self.assertEqual(counts, [1, 0, 0])

    def test_completed_responses_are_immutable_and_deduplicated(self):
        store_response(self.event, 5, self.response(), as_of=self.now, db_path=self.db)
        store_response(self.event, 5, self.response(.9), as_of=self.now, db_path=self.db)
        self.assertEqual((self.stats()['n'], self.stats()['median_residual_return']), (1, .03))
        self.assertFalse(self.stats()['usable'])

    def test_family_statistics_absolute_mean_quantiles_and_direction(self):
        store_response(self.event, 5, self.response(.03), as_of=self.now, db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            catalyst = dict(conn.execute('SELECT * FROM catalysts').fetchone())
            catalyst['catalyst_id'] = 'catalyst:second'
            conn.execute('INSERT INTO catalysts (' + ','.join(catalyst) + ') VALUES (' + ','.join('?' for _ in catalyst) + ')',
                         list(catalyst.values()))
            conn.execute('''INSERT INTO catalyst_response_events SELECT 'catalyst:second', ticker,
                sector_symbol, catalyst_family, direction, event_at, registered_at, state_json
                FROM catalyst_response_events LIMIT 1''')
        store_response({'catalyst_id': 'catalyst:second'}, 5, self.response(-.01), as_of=self.now, db_path=self.db)
        stats = self.stats()
        self.assertEqual((stats['n'], stats['usable']), (2, True))
        self.assertAlmostEqual(stats['expected_abs_residual_move'], .02)
        self.assertAlmostEqual(stats['median_residual_return'], .01)
        self.assertAlmostEqual(stats['p25'], 0)
        self.assertAlmostEqual(stats['p75'], .02)
        self.assertEqual(stats['probability_correct_direction'], .5)

    def store_fixture_history(self, data):
        for role, symbol in (('stock', 'NVDA'), ('market', 'SPY'), ('sector', 'XLK')):
            for timeframe in ('1d', '1h'):
                bars = [{'bar_at': bar['bar_at'], 'available_at': bar['available_at'], 'close': bar['close_price'],
                         'high': bar['high_price'], 'low': bar['low_price']}
                        for bar in data[role] if bar['timeframe'] == timeframe]
                store_history(symbol, bars, provider='fixture', timeframe=timeframe,
                              captured_at=self.now, db_path=self.db)

    def test_collector_completes_untraded_event_with_aligned_prices(self):
        data = histories(self.now)
        event_at = datetime.fromisoformat(data['stock'][-1]['available_at']) + timedelta(hours=1)
        prices = [data[role][-1]['close_price'] for role in data]
        add_point(data, event_at, prices, '1h')
        add_point(data, event_at + timedelta(hours=1), [prices[0] * 1.01, *prices[1:]], '1h')
        for day in (1, 3, 5, 10):
            add_point(data, event_at + timedelta(days=day), [prices[0] * (1 + .01 * day), *prices[1:]])
        self.store_fixture_history(data)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('UPDATE catalyst_response_events SET event_at=?', (event_at.isoformat(),))
        result = collect_catalyst_responses(as_of=self.now, refresh=False, db_path=self.db)
        self.assertTrue(any(row['status'] == 'complete' for row in result['response_coverage']))
        self.assertAlmostEqual(self.stats()['median_residual_return'], .05)
        collect_catalyst_responses(as_of=self.now, refresh=False, db_path=self.db)
        self.assertEqual(self.stats()['n'], 1)

    def test_forward_report_starts_at_frozen_decision_price(self):
        context = self.candidate()
        context['created_at'] = (self.now - timedelta(days=6)).isoformat()
        gate = {'metrics': {'bucket': 'partially_expressed', 'status': 'complete',
                           'endpoint_prices': [110, 400, 200],
                           'betas': {'beta_market': 1.2, 'beta_sector': .4}}}
        record_price_observation(context, gate, db_path=self.db)
        data = {role: [] for role in ('stock', 'market', 'sector')}
        add_point(data, self.now - timedelta(days=6), [100, 400, 200])
        add_point(data, self.now - timedelta(days=1), [121, 400, 200])
        self.store_fixture_history(data)
        capture_forward_returns(as_of=self.now, db_path=self.db)
        bucket = pricing_report(db_path=self.db)['absorption_buckets'][0]
        self.assertEqual(bucket['resolved_candidates'], 1)
        self.assertAlmostEqual(bucket['mean_forward_residual_return'], .1)

    def test_readthrough_candidate_uses_target_company_sector(self):
        context = self.candidate()
        context['catalysts'][0]['sector'] = 'energy'
        record_price_observation(context, {'metrics': {'bucket': 'unknown'}}, db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            snapshot = json.loads(conn.execute('SELECT snapshot_json FROM candidate_price_observations').fetchone()[0])
        self.assertEqual(snapshot['sector_symbol'], 'XLK')

    def test_future_outcome_and_future_capture_are_excluded(self):
        for field in ('outcome', 'recorded'):
            with self.subTest(field=field):
                with closing(get_connection(self.db)) as conn, conn:
                    conn.execute('DELETE FROM catalyst_responses')
                later = self.now + timedelta(days=1)
                response = self.response(outcome_at=(later if field == 'outcome' else self.now).isoformat())
                store_response(self.event, 5, response, as_of=later if field == 'recorded' else self.now, db_path=self.db)
                self.assertEqual(self.stats()['n'], 0)

    def test_target_catalyst_is_excluded_from_own_training_set(self):
        store_response(self.event, 5, self.response(), as_of=self.now, db_path=self.db)
        self.assertEqual(self.stats(exclude_catalyst_id=self.event['catalyst_id'])['n'], 0)

    def test_missing_history_and_pending_coverage_are_explicit(self):
        result = collect_catalyst_responses(as_of=self.now, refresh=False, db_path=self.db)
        self.assertEqual(result['registered_events'], 1)
        states = {row['status'] for row in result['response_coverage']}
        self.assertEqual(states, {'missing_regression_history', 'pending_horizon'})

    def test_collection_budget_and_daily_retry_rotation(self):
        fetched = []
        def fetcher(symbol, timeframe):
            fetched.append((symbol, timeframe))
            return 'fixture', []
        policy = replace(PricingPolicy(), symbol_budget=1)
        first = refresh_universe_history(as_of=self.now, policy=policy, fetcher=fetcher, db_path=self.db)
        second = refresh_universe_history(as_of=self.now, policy=policy, fetcher=fetcher, db_path=self.db)
        self.assertEqual((first['attempted_symbols'], first['deferred_symbols'], len(fetched)), (1, 2, 4))
        self.assertNotEqual(fetched[0][0], fetched[2][0])
        self.assertEqual(second['deferred_symbols'], 1)

    def test_provider_failure_records_coverage_without_aborting(self):
        def fetcher(symbol, timeframe):
            raise RuntimeError('fixture provider unavailable')
        result = collect_catalyst_responses(as_of=self.now, fetcher=fetcher,
                                            policy=replace(PricingPolicy(), symbol_budget=1), db_path=self.db)
        self.assertEqual(result['history_refreshes'][0]['status'], 'partial_or_missing')

    def test_newly_imported_history_is_not_visible_to_earlier_candidate(self):
        store_history('NVDA', [{'bar_at': (self.now - timedelta(days=1)).isoformat(), 'close': 100}],
                      provider='fixture', timeframe='1d', captured_at=self.now + timedelta(days=1), db_path=self.db)
        data = load_histories('NVDA', 'XLK', as_of=self.now, captured_before=self.now.isoformat(), db_path=self.db)
        self.assertEqual(data['stock'], [])

    def candidate(self):
        return record_candidate('NVDA', {'pipeline_version': 'fixture-v4'}, db_path=self.db)

    def test_unknown_candidate_pricing_is_frozen_with_hash_and_coverage(self):
        context = self.candidate()
        gate = record_candidate_price_response(context, db_path=self.db)
        self.assertTrue(gate['allowed'])
        self.assertIsNone(gate['metrics']['price_absorption_ratio'])
        with closing(get_connection(self.db)) as conn, conn:
            row = conn.execute("SELECT snapshot_hash, snapshot_json FROM candidate_snapshots WHERE stage='PRICE_ABSORPTION'").fetchone()
        from hashlib import sha256
        self.assertEqual(row['snapshot_hash'], sha256(row['snapshot_json'].encode()).hexdigest())
        self.assertEqual(pricing_report(db_path=self.db)['absorption_buckets'][0]['resolved_candidates'], 0)

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

    def test_migration_is_repeatable_and_preserves_existing_articles(self):
        initialize_database(db_path=self.db)
        initialize_database(db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM news_articles').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
