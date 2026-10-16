"""Phase 5: trade-economics reports, the TRADE_ECONOMICS gate and the submission recheck."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import logging
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database
from market_db_modules.candidate_lifecycle import record_candidate
from market_db_modules.trade_economics import get_candidate_economics_report
from news_db_modules.catalysts import build_catalyst_state, store_catalyst_state
from services.option_pricing import black_scholes_price
from services.order_candidates import OrderCandidateBuilder
from services.trade_economics import (
    apply_trade_economics,
    recheck_trade_economics,
    scenario_set,
    selected_row,
)
from services.trade_economics_policy import TradeEconomicsPolicy


NOW = datetime(2026, 9, 21, 15, tzinfo=timezone.utc)
CATALYST = 'catalyst:economics-fixture'
SYMBOL = 'NVDA261016C00101000'
LOGGER = 'services.trade_economics'


def quote(*, bid=2.95, ask=3.05, at=NOW):
    return {'bid_price': bid, 'ask_price': ask, 'bid_size': 10, 'ask_size': 10, 'timestamp': at.isoformat()}


def contract(*, symbol=SYMBOL, strike=101, side='call', **quoted):
    return {'symbol': symbol, 'option_id': 1, 'contract_type': side, 'strike_price': strike,
            'expiration_date': '2026-10-16', 'implied_volatility': .4,
            'greeks': {'delta': .48, 'gamma': .04, 'vega': .11, 'theta': -.06}, 'latest_quote': quote(**quoted)}


def scenario_rows(returns, *, same_day=False):
    return [{'catalyst_id': f'catalyst:s{index}', 'ticker': f'T{index}', 'stock_return': value,
             'residual_return': value, 'event_at': (NOW - timedelta(days=60 + (0 if same_day else index))).isoformat()}
            for index, value in enumerate(returns)]


def database(test):
    temp = tempfile.TemporaryDirectory()
    test.addCleanup(temp.cleanup)
    db = Path(temp.name) / 'economics.db'
    initialize_database(db_path=db)
    with closing(get_connection(db)) as conn, conn:
        conn.execute("INSERT INTO sectors(id,sector_key,name) VALUES (1,'technology','Technology')")
        conn.execute("INSERT INTO industries(id,sector_id,industry_key,name) VALUES (1,1,'chips','Chips')")
        conn.execute("INSERT INTO companies(id,industry_id,symbol,name) VALUES (1,1,'NVDA','Fixture')")
        state = build_catalyst_state(CATALYST, [], ticker='NVDA')
        state.update(catalyst_family='earnings_positive', direction='positive', latest_root_event_time=NOW.isoformat())
        store_catalyst_state(state, conn=conn)
    return db


def seed_scenarios(db, returns, *, horizon=10, same_day=False):
    with closing(get_connection(db)) as conn, conn:
        for row in scenario_rows(returns, same_day=same_day):
            state = build_catalyst_state(row['catalyst_id'], [], ticker=row['ticker'])
            state.update(catalyst_family='earnings_positive', direction='positive')
            store_catalyst_state(state, conn=conn)
            outcome = (datetime.fromisoformat(row['event_at']) + timedelta(days=horizon)).isoformat()
            conn.execute('INSERT INTO catalyst_response_events VALUES (?,?,?,?,?,?,?,?)',
                         (row['catalyst_id'], row['ticker'], None, 'earnings_positive', 'positive',
                          row['event_at'], row['event_at'], '{}'))
            conn.execute('INSERT INTO catalyst_responses VALUES (?,?,?,?,?,?,?)',
                         (row['catalyst_id'], horizon, 'complete', outcome, outcome, row['residual_return'],
                          json.dumps({'stock_return': row['stock_return']})))


def candidate_context(db):
    context = record_candidate('NVDA', {'pipeline_version': 'economics-fixture', 'gate_config': {}}, evidence=[], db_path=db)
    return {**context, 'catalysts': [{'catalyst_id': CATALYST, 'catalyst_family': 'earnings_positive',
                                      'direction': 'positive', 'expected_horizon': '4_10_days'}],
            'price_absorption_gate': {'metrics': {'atr_fraction': .02}}}


def manager_result(context, *, contracts=None, decision='call', bucket='20_30'):
    contracts = contracts or [contract()]
    return {'company': {'company_id': 1, 'symbol': 'NVDA'}, 'candidate_context': context,
            'candidate_id': (context or {}).get('candidate_id'),
            'recommendation': {'decision': decision, 'confidence': 'high', 'target_dte_bucket': bucket},
            'strategist_recommendation': {'decision': 'trade_candidate', 'confidence': 'high'},
            'market_context': {'current_stock_price': {'price': 100.0, 'timestamp': NOW.isoformat()},
                               'option_market': {'contracts': contracts}},
            'selected_option': contracts[0], 'entry_gate': {'allowed': True}, 'market_regime_gate': {'allowed': True}}


class EconomicsFixture(unittest.TestCase):
    def setUp(self):
        self.db = database(self)
        self.context = candidate_context(self.db)

    def economics(self, returns=None, *, mode='shadow', same_day=False, **result):
        if returns:
            seed_scenarios(self.db, returns, same_day=same_day)
        outcome = apply_trade_economics(manager_result(self.context, **result),
                                        policy=TradeEconomicsPolicy(mode=mode), as_of=NOW, db_path=self.db)
        return outcome['trade_economics_gate']

    def report(self, gate):
        return get_candidate_economics_report(gate['report_id'], db_path=self.db)


class OptionPricingTests(unittest.TestCase):
    TERMS = {'spot': 100, 'strike': 105, 'years': .1, 'rate': .04}

    def test_put_call_parity(self):
        call = black_scholes_price('call', volatility=.3, **self.TERMS)
        put = black_scholes_price('put', volatility=.3, **self.TERMS)
        self.assertAlmostEqual(call - put, 100 - 105 * math.exp(-.04 * .1), places=10)


class ScenarioWeightTests(unittest.TestCase):
    def test_same_day_events_share_one_cluster(self):
        self.assertEqual(scenario_set(scenario_rows([.01] * 3, same_day=True), 10)['effective_sample_size'], 1)


class ReportTests(EconomicsFixture):
    def test_attractive_option_allows_the_trade(self):
        gate = self.economics([.04] * 31)
        self.assertEqual((gate['would_allow'], gate['outcome']), (True, 'trade'))

    def test_credible_thesis_with_unattractive_option_is_no_trade(self):
        gate = self.economics([.005] * 31, mode='enforce')
        self.assertEqual((gate['allowed'], gate['eligible_decision'], gate['outcome'], gate['reason_codes']),
                         (False, 'neither', 'no_trade', ['economics_expected_return_below_minimum']))

    def test_missing_scenarios_never_produce_an_expected_value(self):
        gate = self.economics()
        self.assertEqual((gate['would_allow'], gate['evidence']['expected_net_pnl'], selected_row(self.report(gate))['status']),
                         (False, None, 'no_scenarios'))

    def test_adverse_scenarios_respect_the_premium_loss_bound(self):
        report = self.report(self.economics([-.6, -.2, 0.0, .3] * 8))
        row = selected_row(report)
        bound = -(row['entry']['ask'] * 100 + report['assumptions']['policy']['fee_per_contract_side'])
        worst = min([row['outcomes']['scenario_range']['minimum']] + [stress['net_pnl'] for stress in row['stress']])
        self.assertAlmostEqual(worst, bound, places=9)

    def test_unpersisted_report_cannot_authorize(self):
        self.context = {**self.context, 'candidate_id': None}
        gate = self.economics([.04] * 31, mode='enforce')
        self.assertEqual((gate['allowed'], gate['reason_codes']), (False, ['economics_report_unpersisted']))

    def test_report_failure_is_a_blocking_verdict(self):
        with patch('services.trade_economics.build_trade_economics_report', side_effect=RuntimeError('boom')), \
                self.assertLogs(LOGGER, logging.ERROR):
            gate = self.economics(mode='enforce')
        self.assertEqual((gate['allowed'], gate['reason_codes']), (False, ['economics_check_error']))

    def test_shadow_mode_records_without_blocking(self):
        gate = self.economics([.005] * 31)
        self.assertEqual((gate['allowed'], gate['would_allow']), (True, False))


class PersistenceTests(EconomicsFixture):
    def test_reports_are_immutable(self):
        gate = self.economics([.04] * 31)
        with closing(get_connection(self.db)) as conn, self.assertRaises(sqlite3.DatabaseError):
            conn.execute("UPDATE candidate_economics_reports SET outcome='trade' WHERE report_id=?", (gate['report_id'],))


class PolicyTests(unittest.TestCase):
    def test_enforce_without_promotion_proof_stays_shadow(self):
        with patch.dict('os.environ', {'TRADE_ECONOMICS_MODE': 'enforce'}), \
                patch('services.research_promotion.approved', return_value=False), self.assertLogs(level='WARNING'):
            self.assertEqual(TradeEconomicsPolicy.from_env().mode, 'shadow')


class SubmissionRecheckTests(EconomicsFixture):
    def setUp(self):
        super().setUp()
        self.gate = self.economics([.04] * 31, mode='enforce')

    def recheck(self, *, after=30, option=None, spot=100.0, gate=None):
        at = NOW + timedelta(seconds=after)
        candidate = {'candidate_id': self.context['candidate_id'], 'selected_option_symbol': SYMBOL,
                     'trade_economics_gate': gate or self.gate}
        arrival = {'option': option or quote(at=at), 'underlying': {'price': spot, 'timestamp': at.isoformat()}}
        return recheck_trade_economics(candidate, arrival, as_of=at, db_path=self.db)

    def test_valid_report_passes(self):
        verdict = self.recheck()
        self.assertEqual((verdict['allowed'], verdict['reason_codes']), (True, []))

    def test_expired_report_cannot_authorize_an_enforced_entry(self):
        verdict = self.recheck(after=901)
        self.assertEqual((verdict['allowed'], verdict['reason_codes']), (False, ['economics_report_expired']))

    def test_underlying_move_beyond_half_an_atr_blocks(self):
        self.assertIn('economics_underlying_moved', self.recheck(spot=102.0)['reason_codes'])

    def test_wider_arrival_spread_exhausts_the_edge(self):
        at = NOW + timedelta(seconds=30)
        self.assertEqual(self.recheck(option=quote(bid=2.0, ask=4.0, at=at))['reason_codes'], ['economics_edge_exhausted'])

    def test_enforced_candidate_without_a_report_is_blocked(self):
        with patch('services.trade_economics.TradeEconomicsPolicy.from_env', return_value=TradeEconomicsPolicy(mode='enforce')):
            verdict = recheck_trade_economics({'selected_option_symbol': SYMBOL}, {}, as_of=NOW, db_path=self.db)
        self.assertEqual((verdict['allowed'], verdict['reason_codes']), (False, ['economics_report_missing']))


class OrderEligibilityTests(unittest.TestCase):
    def company_result(self, **gate):
        return {'decision': 'call', 'confidence': 'high', 'strategist_recommendation': {'decision': 'trade_candidate'},
                'entry_gate': {'allowed': True}, 'market_regime_gate': {'allowed': True}, **gate}

    def test_blocked_economics_gate_blocks_the_order(self):
        result = self.company_result(trade_economics_gate={'allowed': False})
        self.assertFalse(OrderCandidateBuilder()._is_trade_eligible(result))


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        from tests.test_trade_executor_portfolio_guardrails import OptionTradeExecutorPortfolioGuardrailTests
        self.fixture = OptionTradeExecutorPortfolioGuardrailTests()
        self.fixture.setUp()
        self.fixture.gateway.submit_option_market_order.return_value = {'id': 'order-1'}

    def execute(self, verdict):
        with patch('services.trade_executor.recheck_trade_economics', return_value=verdict), \
                patch('services.risk_clusters.get_risk_cluster', return_value='mega_cap_tech'):
            return self.fixture.executor._execute_candidate(trading_client=object(), candidate=self.fixture._candidate(),
                                                            session=self.fixture._session())

    def test_failed_enforced_recheck_never_reaches_the_broker(self):
        execution = self.execute({'allowed': False, 'would_allow': False, 'reason_codes': ['economics_report_expired'],
                                  'metrics': {}})
        self.assertEqual((execution['submitted'], execution['portfolio_guardrail']), (False, 'economics_recheck_failed'))

    def test_shadow_recheck_failure_still_submits(self):
        execution = self.execute({'allowed': True, 'would_allow': False, 'reason_codes': ['economics_report_expired'],
                                  'metrics': {}})
        self.assertTrue(execution['submitted'])


class StreamingFinalizationTests(unittest.TestCase):
    def setUp(self):
        from agentCallers import main as runner
        self.runner = runner
        self.events = []
        manager = {'company': {'symbol': 'AAPL'}, 'recommendation': {'decision': 'call', 'confidence': 'high'}}
        patches = {'build_market_regime_snapshot': {'return_value': {'state': 'trend_up', 'as_of': NOW.isoformat()}},
                   'record_manager_decision_history': {'return_value': 1},
                   'decide_company_purchase': {'return_value': {'company': {'symbol': 'AAPL'}, 'recommendation': {}}},
                   'decide_company_option_position': {'side_effect': lambda symbol, **_: dict(manager)},
                   'apply_deterministic_option_selection': {'side_effect': lambda result: result},
                   'apply_trade_economics': {'side_effect': self.economics}}
        for name, options in patches.items():
            patcher = patch.object(runner, name, **options)
            patcher.start()
            self.addCleanup(patcher.stop)

    def economics(self, result, *, policy):
        self.events.append('economics')
        return {**result, 'trade_economics_gate': {'allowed': True, 'would_allow': True}}

    def test_execution_callback_only_sees_finalized_results(self):
        received = []
        self.runner._run_strategist_and_manager(
            ['AAPL'], on_manager_result=lambda result: (self.events.append('execute'), received.append(result)))
        self.assertEqual((self.events, 'trade_economics_gate' in received[0]), (['economics', 'execute'], True))


if __name__ == '__main__':
    unittest.main()
