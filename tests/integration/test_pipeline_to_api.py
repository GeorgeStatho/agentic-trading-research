"""The decision pipeline on collected news, read back through the database and the dashboard API.

Real ranking, admission, strategist and manager stages and contract selection, with only the
outside world faked (tests/integration/fakes.py). No orders: the executor is covered by tests/e2e.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db_common import get_connection, use_database
from market_db_modules.bootstrap import initialize_database as initialize_market_database
from news_db_modules.bootstrap import initialize_database as initialize_news_database
from tests.integration.fakes import (
    SYMBOL, FakeMarket, ScriptedModel, add_company, manager_reply, offline_services, seed_cycle_inputs,
    strategist_reply,
)


class PipelineToApiTests(unittest.TestCase):
    """AMD ranks first but its strategist answer cites another company; INTC has only medium-confidence news."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.enterClassContext(use_database(Path(tmp.name) / "pipeline.db"))
        initialize_market_database()
        initialize_news_database()
        seed_cycle_inputs()
        add_company(2, "AMD", (3,), market_weight=2.0)
        add_company(3, "INTC", (4,), confidence="medium", market_weight=0.2)
        # Every strategist answer is grounded on NVDA, so the real grounding check rejects AMD's.
        cls.model = ScriptedModel(strategist=strategist_reply(), manager=manager_reply("call"))
        from agentCallers import decision_pipeline
        with offline_services(FakeMarket(), cls.model):
            cls.result = decision_pipeline.run_full_agent_stack_from_existing_data()

    def prompted(self, stage):
        # A prompt names its own company first; industry peers follow.
        return [re.search(r'"symbol":\s*"([A-Z]+)"', prompt).group(1)
                for asked, prompt in self.model.prompts if asked == stage]

    def test_a_company_without_high_confidence_news_is_skipped_before_any_model_call(self):
        skipped = [(row["symbol"], row["skip_reason"]) for row in self.result["skipped_companies_before_strategist_manager"]]
        self.assertEqual(skipped, [("INTC", "insufficient_high_confidence_articles")])
        self.assertNotIn("INTC", self.prompted("strategist") + self.prompted("manager"))

    def test_a_strategist_failure_is_recorded_and_the_next_company_still_runs(self):
        self.assertEqual([(row["symbol"], row["stage"]) for row in self.result["failed_companies"]], [("AMD", "STRATEGIST")])
        # Asked once more after the first invalid answer, then NVDA runs through both stages.
        self.assertEqual(self.prompted("strategist"), ["AMD", "AMD", SYMBOL])
        self.assertEqual(self.prompted("manager"), [SYMBOL])

    def test_the_manager_decision_is_recorded_once_and_served_as_recorded(self):
        [manager] = self.result["manager"]
        recommendation = manager["recommendation"]
        self.assertEqual((recommendation["decision"], manager["company"]["symbol"]), ("call", SYMBOL))
        self.assertIsNotNone(recommendation["selected_option_id"])
        # The manager is handed the strategist run it builds on, with the articles that run read.
        [strategist] = [row for row in self.result["strategist"] if row["company"]["symbol"] == SYMBOL]
        reference = manager["strategist_recommendation"]["strategist_run_reference"]
        self.assertEqual(reference, strategist["strategist_run_reference"])
        self.assertLessEqual({1, 2}, set(reference["input_article_ids"]))
        self.assertEqual(self.result["selected_options"]["selected_option_count"], 1)

        with closing(get_connection()) as conn:
            rows = conn.execute("""SELECT id, symbol, strategist_decision, manager_decision, selected_expiration_date,
                                          selected_strike_price FROM manager_decision_history""").fetchall()
        self.assertEqual([tuple(row) for row in rows],
                         [(manager["manager_decision_history_id"], SYMBOL, "trade_candidate", "call",
                           recommendation["selected_expiration_date"], recommendation["selected_strike_price"])])

        import api
        response = api.app.test_client().get("/api/company-decisions")
        self.assertEqual(response.status_code, 200)
        served = {row["symbol"]: row for row in response.get_json()["companies"]}[SYMBOL]
        self.assertEqual((served["symbol"], served["manager"]["decision"], served["manager"]["manager_decision_history_id"],
                          served["manager"]["selected_strike_price"]),
                         (SYMBOL, "call", manager["manager_decision_history_id"], recommendation["selected_strike_price"]))


if __name__ == "__main__":
    unittest.main()
