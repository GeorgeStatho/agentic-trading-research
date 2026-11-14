"""One worker trading cycle, from collected news in the database to the dashboard API.

Everything the project owns runs for real: ranking, admission, strategist and manager stages,
contract selection, gates, order candidates, the trade executor, journals and the Flask API.
Only the outside world is faked (tests/integration/fakes.py). Run with:

    python3 scripts/project.py test --e2e
"""
from __future__ import annotations

import dataclasses
import logging
import os
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from db_common import get_connection
from tests.db_fixtures import temporary_database
from tests.integration.fakes import (
    SYMBOL, FakeMarket, ScriptedModel, manager_reply, offline_services, seed_cycle_inputs, strategist_reply,
)

# The worker never opens new positions on a Friday; pin a mid-week market morning.
WEDNESDAY_MORNING = datetime(2026, 11, 4, 11, 0, tzinfo=ZoneInfo("America/New_York"))
OUTPUT_ENV = ("SCRIPT_STATUS_PATH", "TRADE_EXECUTION_OUTPUT_PATH", "OPTION_POSITION_MANAGEMENT_OUTPUT_PATH",
              "OPTION_POSITION_STATE_PATH", "AGENT_OUTPUT_PATH", "SELECTED_OPTIONS_OUTPUT_PATH")


E2E_ONLY = unittest.skipUnless(os.environ.get("RUN_E2E"), "end-to-end tests run with: scripts/project.py test --e2e")


class TradingCycleHarness(unittest.TestCase):
    """A seeded database, a fake market and a real worker; subclasses add the tests."""

    def setUp(self):
        temporary_database(self)
        outputs = tempfile.TemporaryDirectory()
        self.addCleanup(outputs.cleanup)
        self.outputs = Path(outputs.name)
        self.enterContext(mock.patch.dict(os.environ, {name: str(self.outputs / f"{name.lower()}.json")
                                                       for name in OUTPUT_ENV}))
        seed_cycle_inputs()
        self.market = FakeMarket()

    def worker(self):
        from services.core.trading_config import WorkerPaths, WorkerSettings
        from services.core.status_files import StatusReporter
        from services.runtime.cold_start import ColdStartSanityChecker
        from services.runtime.worker_application import WorkerApplication
        from services.thesis.thesis_capture import PositionThesisJournal
        from services.trading.order_candidates import OrderCandidateBuilder
        from services.trading.trade_executor import OptionTradeExecutor
        from services.trading.trade_journal import OptionTradeJournal
        from services.trading.trading_gateway import AlpacaTradingGateway

        logger = logging.getLogger("e2e")
        # combined_output_path has no environment override; keep it out of the checkout's Data/.
        paths = dataclasses.replace(WorkerPaths.from_env(), combined_output_path=self.outputs / "combined.json")
        settings = WorkerSettings.from_env()
        gateway = AlpacaTradingGateway(paper=True, logger=logger)
        executor = OptionTradeExecutor(
            settings=settings, trading_gateway=gateway, trade_journal=OptionTradeJournal(paper=True, logger=logger),
            thesis_journal=PositionThesisJournal(logger=logger), logger=logger)
        self.enterContext(mock.patch.object(WorkerApplication, "_market_now", staticmethod(lambda: WEDNESDAY_MORNING)))
        return WorkerApplication(
            paths=paths, settings=settings, status_reporter=StatusReporter(paths.status_path),
            cold_start_checker=ColdStartSanityChecker(logger), trading_gateway=gateway,
            order_candidate_builder=OrderCandidateBuilder(
                execute_medium_confidence_trades=settings.execute_medium_confidence_trades),
            trade_executor=executor, position_manager=None, logger=logger)

    def run_cycle(self, model, *, streaming=False):
        worker = self.worker()
        with offline_services(self.market, model):
            run = worker.run_streaming_trading_cycle if streaming else worker.run_trading_cycle
            return run(trading_client=self.market)

    def api(self):
        import api
        from api_routes import trades as trade_routes
        from api_support import trades as trade_support
        # The API resolves output paths at import, which may predate this test's environment.
        output = Path(os.environ["TRADE_EXECUTION_OUTPUT_PATH"])
        self.enterContext(mock.patch.object(trade_routes, "TRADE_EXECUTION_OUTPUT_PATH", output))
        self.enterContext(mock.patch.object(trade_support, "TRADE_EXECUTION_OUTPUT_PATH", output))
        self.enterContext(mock.patch.object(trade_support, "SELECTED_OPTIONS_OUTPUT_PATH",
                                            Path(os.environ["SELECTED_OPTIONS_OUTPUT_PATH"])))
        return api.app.test_client()

    def get(self, client, route):
        response = client.get(route)
        self.assertEqual(response.status_code, 200, route)
        return response.get_json()



@E2E_ONLY
class TradingCycleEndToEndTests(TradingCycleHarness):
    def assert_one_call_traded_and_served(self, result, model):
        self.assertEqual([stage for stage, _ in model.prompts], ["strategist", "manager"])
        self.assertEqual(len(self.market.orders), 1)
        order = self.market.orders[0]
        self.assertTrue(order.symbol.startswith(SYMBOL) and order.symbol[len(SYMBOL) + 6] == "C", order.symbol)
        self.assertEqual(result["trade_result"]["submitted_count"], 1)
        with closing(get_connection()) as conn:
            history = conn.execute("SELECT symbol, trade_executed, trade_execution_order_id FROM manager_decision_history").fetchall()
        self.assertEqual([tuple(row) for row in history], [(SYMBOL, 1, order.id)])

        client = self.api()
        decisions = self.get(client, "/api/company-decisions")["companies"]
        self.assertEqual([(row["symbol"], row["manager"]["decision"]) for row in decisions], [(SYMBOL, "call")])
        trades = self.get(client, "/api/executed-trades")
        self.assertEqual([trade["option_symbol"] for trade in trades["trades"]], [order.symbol])
        explanation = self.get(client, "/api/why-bot-traded")
        self.assertEqual((explanation["ticker"], explanation["contract"]["symbol"], explanation["submitted"]),
                         (SYMBOL, order.symbol, True))
        self.assertEqual(explanation["strategist_decision"], "trade_candidate")
        self.assertEqual(self.get(client, "/api/trade-execution-output")["submitted_count"], 1)

    def test_batch_cycle_buys_the_selected_call_and_the_dashboard_reports_it(self):
        model = ScriptedModel(strategist=strategist_reply(), manager=manager_reply("call"))
        self.assert_one_call_traded_and_served(self.run_cycle(model), model)

    def test_streaming_cycle_buys_as_soon_as_the_manager_decides(self):
        model = ScriptedModel(strategist=strategist_reply(), manager=manager_reply("call"))
        self.assert_one_call_traded_and_served(self.run_cycle(model, streaming=True), model)

    def test_a_declined_company_places_no_order_but_its_decision_is_still_served(self):
        model = ScriptedModel(strategist=strategist_reply("watchlist"), manager=manager_reply("call"))
        result = self.run_cycle(model)
        self.assertEqual([stage for stage, _ in model.prompts], ["strategist"])
        self.assertEqual((self.market.orders, result["trade_result"]["submitted_count"]), ([], 0))
        decisions = self.get(self.api(), "/api/company-decisions")["companies"]
        self.assertEqual([(row["symbol"], row["manager"]["decision"]) for row in decisions], [(SYMBOL, "neither")])


if __name__ == "__main__":
    unittest.main()
