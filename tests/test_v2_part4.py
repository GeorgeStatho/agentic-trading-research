from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import tempfile
import unittest

from db_common import get_connection
from market_db import initialize_database
from news_db import initialize_database as initialize_news, add_company_news_article
from market_db_modules.candidate_lifecycle import record_candidate
from market_db_modules.pricing_research import (
    catalyst_stats,
    register_response_events,
    store_response,
    store_history,
)
from services.option_hurdle_gate import evaluate_option_hurdle
from catalyst_models import classify_catalyst
from services.market_regime_gate import evaluate_market_regime_gate, MarketRegimePolicy, apply_market_regime_gate_to_manager_result
from services.pricing_policy import PricingPolicy
from services.pricing_gates import apply_option_hurdle_to_manager_result

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

    def test_migration_is_repeatable_and_preserves_existing_articles(self):
        initialize_database(db_path=self.db)
        initialize_database(db_path=self.db)
        with closing(get_connection(self.db)) as conn, conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM news_articles').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
