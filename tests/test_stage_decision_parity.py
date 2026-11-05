"""Live runs and replays share one stage decision.

Each test runs the live stage, takes the payload it froze, replays it, and checks that the
replay asked the model the identical prompt and returned the identical recommendation.
Offline only: the model is a stub that records its prompts.
"""
import copy
import json
import unittest
from functools import partial
from unittest.mock import patch

import tests.test_thesis_evidence_contract as thesis_tests
from agent_stages import manager_runner, strategist_runner
from services.thesis.evidence_thesis import check_evidence_thesis
from services.thesis.evidence_thesis_policy import EvidenceThesisPolicy


def manager_payload(*, evidence=True):
    payload = {
        "company": {"company_id": 1, "symbol": "AAPL", "name": "Apple", "historical_price_data": {}},
        "views": {"company": {"count": 2 if evidence else 0}},
        "supporting_articles": {"article_summaries": [{"article_id": 7, "title": "Guidance raised"}] if evidence else [],
                                "full_articles": []},
        "market_context": {"option_market": {"contract_count": 0, "contracts": []}},
    }
    return payload


MANAGER_JSON = json.dumps({"recommendation": {
    "decision": "call", "confidence": "high", "target_dte_bucket": "dte_8_21",
    "reason": "Raised guidance with confirming price action."}})
MANAGER_PROSE = "Recommendation: put\nConfidence: medium\nReason: Guidance cut and weak tape."


class StubModel:
    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def __call__(self, *, client, model, system_prompt, user_prompt, **_):
        self.prompts.append((system_prompt, user_prompt))
        return self.answer


def without_selection_debug(recommendation):
    return {key: value for key, value in recommendation.items() if key != "selection_debug"}


class ManagerParityTests(unittest.TestCase):
    def run_live(self, payload, stub, **kwargs):
        with patch.object(manager_runner, "build_manager_input", return_value=copy.deepcopy(payload)), \
                patch.object(manager_runner, "ask_model", side_effect=stub), \
                patch.object(manager_runner, "_refresh_market_context_for_target_dte_bucket",
                             side_effect=lambda *, market_context, **_: (market_context, {})), \
                patch.object(manager_runner, "evaluate_evidence_continuation_gate", return_value={}):
            return manager_runner.decide_company_option_position("AAPL", client=object(), model="test-model", **kwargs)

    def replay(self, payload, stub):
        with patch.object(manager_runner, "ask_model", side_effect=stub):
            return manager_runner.replay_manager_decision_from_payload(payload, client=object(), model="test-model")

    def assert_parity(self, answer):
        live_model, replay_model = StubModel(answer), StubModel(answer)
        live = self.run_live(manager_payload(), live_model)
        replay = self.replay(live["manager_replay_payload"], replay_model)
        self.assertEqual(replay_model.prompts, live_model.prompts)
        self.assertEqual(replay["recommendation"], without_selection_debug(live["recommendation"]))
        return replay["recommendation"]

    def test_json_answer_matches(self):
        self.assertEqual(self.assert_parity(MANAGER_JSON)["decision"], "call")

    def test_unparseable_answer_fails_the_same_way(self):
        live_model, replay_model = StubModel("not a recommendation"), StubModel("not a recommendation")
        with self.assertLogs(manager_runner.LOGGER, "ERROR"), self.assertRaises(RuntimeError) as live_error:
            self.run_live(manager_payload(), live_model)
        with self.assertLogs(manager_runner.LOGGER, "ERROR"), self.assertRaises(RuntimeError) as replay_error:
            self.replay(manager_payload(), replay_model)
        self.assertEqual(str(replay_error.exception), str(live_error.exception))


    # A strategist watchlist or do_not_trade answer is final: the manager model is never asked.
    def test_a_declined_company_skips_the_manager_live_and_in_replay(self):
        for decision in ("watchlist", "do_not_trade"):
            with self.subTest(decision=decision):
                live_model, replay_model = StubModel(MANAGER_JSON), StubModel(MANAGER_JSON)
                live = self.run_live(manager_payload(), live_model,
                                     strategist_recommendation={"decision": decision, "confidence": "medium"})
                recommendation = live["recommendation"]
                self.assertEqual(live_model.prompts, [])
                self.assertEqual((recommendation["decision"], recommendation["selected_option_source"]),
                                 ("neither", "strategist_declined"))
                self.assertIn(decision, recommendation["reason"])
                self.assertIsNone(live["selected_option"])
                self.assertEqual(live["manager_replay_payload"]["strategist_recommendation"]["decision"], decision)
                replay = self.replay(live["manager_replay_payload"], replay_model)
                self.assertEqual(replay_model.prompts, [])
                self.assertEqual(replay["replay_skipped"], "strategist_declined")
                self.assertEqual(replay["recommendation"], without_selection_debug(recommendation))

    # History, the dashboard and the evidence-thesis gate read these from the result, skipped or not.
    def test_a_declined_result_keeps_the_strategist_answer_and_context(self):
        live = self.run_live(manager_payload(), StubModel(MANAGER_JSON),
                             strategist_recommendation={"decision": "watchlist", "confidence": "medium"})
        self.assertEqual(live["strategist_recommendation"]["decision"], "watchlist")
        self.assertLessEqual({"supporting_articles", "company_evidence", "recent_manager_decision_history"},
                             live.keys())

    def test_a_trade_candidate_still_asks_the_manager(self):
        live_model = StubModel(MANAGER_JSON)
        live = self.run_live(manager_payload(), live_model,
                             strategist_recommendation={"decision": "trade_candidate", "confidence": "high"})
        self.assertEqual(len(live_model.prompts), 1)
        self.assertEqual(live["recommendation"]["decision"], "call")


class StrategistParityTests(thesis_tests.ThesisFixture):
    strategist_payload = thesis_tests.StrategistIntegrationTests.strategist_payload
    response = thesis_tests.StrategistIntegrationTests.response

    def setUp(self):
        super().setUp()
        self.thesis_policy = EvidenceThesisPolicy(mode="shadow")
        patcher = patch.object(strategist_runner, "check_evidence_thesis",
                               partial(check_evidence_thesis, db_path=self.db))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_live(self, payload, stub):
        saved = {}

        def save(**kwargs):
            saved.update(kwargs)
            return {"id": 1}

        with patch.object(strategist_runner, "build_strategist_input", return_value=copy.deepcopy(payload)), \
                patch.object(strategist_runner, "ask_model", side_effect=stub), \
                patch.object(strategist_runner, "_save_strategist_summary", side_effect=save), \
                patch.object(strategist_runner, "persist_thesis_check", return_value="thesis:fixture"):
            result = strategist_runner.decide_company_purchase("NVDA", client=object(), model="test-model",
                                                               thesis_policy=self.thesis_policy)
        return result, saved

    def replay(self, payload, stub):
        with patch.object(strategist_runner, "ask_model", side_effect=stub):
            return strategist_runner.replay_strategist_decision_from_payload(
                payload, client=object(), model="test-model", thesis_policy=self.thesis_policy)

    def test_grounded_answer_and_thesis_check_match(self):
        payload = self.strategist_payload()
        live_model, replay_model = StubModel(self.response(payload)), StubModel(self.response(payload))
        live, saved = self.run_live(payload, live_model)
        replay = self.replay(saved["input_payload"], replay_model)
        self.assertEqual(replay_model.prompts, live_model.prompts)
        self.assertEqual(live["recommendation"], strategist_runner.attach_thesis_check(
            replay["recommendation"], replay["thesis_check"], "thesis:fixture"))
        self.assertEqual(saved["raw_response"], self.response(payload))

    def test_uncited_answer_is_recorded_live_and_raised_in_replay(self):
        payload = self.strategist_payload()
        prose = "Recommendation: buy\nConfidence: high\nSummary: Fresh evidence supports a bullish setup."
        with patch.object(strategist_runner, "_record_failed_strategist_run") as record_failed, \
                self.assertRaises(Exception) as live_error:
            self.run_live(payload, StubModel(prose))
        self.assertEqual(record_failed.call_args.kwargs["raw_response"], prose)
        with self.assertRaises(type(live_error.exception)):
            self.replay(payload, StubModel(prose))


if __name__ == "__main__":
    unittest.main()
