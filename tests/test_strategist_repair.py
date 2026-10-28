"""One repair attempt when a strategist answer fails validation, and lenient JSON parsing, with no network."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_helpers.text_utils import extract_json_value
from agent_stages import strategist_runner
from tests.test_strategist_grounding import grounded_recommendation

PAYLOAD = {
    "company": {"company_id": 7, "symbol": "AAPL"},
    "grounding": {"company": {"symbol": "AAPL", "id": 7}, "industry": {"id": 3}, "sector": {"id": 1},
                  "article_ids": [42]},
}
NO_THESIS = SimpleNamespace(requests_thesis=False)


def answer(recommendation):
    return json.dumps({"recommendation": recommendation})


def short_of_one_risk_citation():
    recommendation = grounded_recommendation()
    recommendation["risks"] = ["The catalyst may not persist.", "Margins could compress."]
    return recommendation


def decide(*answers):
    with patch.object(strategist_runner, "payload_has_evidence", return_value=True), \
            patch.object(strategist_runner, "build_strategist_prompt", return_value=("system", "user")), \
            patch.object(strategist_runner, "ask_model", side_effect=list(answers)) as ask:
        try:
            return strategist_runner.decide_strategist_recommendation(
                json.loads(json.dumps(PAYLOAD)), client=None, model="model", thesis_policy=NO_THESIS), ask
        except Exception as exc:
            exc.ask = ask
            raise


class StrategistRepairTests(unittest.TestCase):
    def test_valid_answer_is_used_without_a_second_call(self):
        decision, ask = decide(answer(grounded_recommendation()))
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(decision["recommendation"]["decision"], "trade_candidate")
        self.assertNotIn("repair_attempt", decision["response_diagnostic"])

    def test_rejected_answer_gets_one_attempt_told_what_failed(self):
        repaired = answer(grounded_recommendation())
        decision, ask = decide(answer(short_of_one_risk_citation()), repaired)
        self.assertEqual(ask.call_count, 2)
        repair_prompt = ask.call_args_list[1].kwargs["user_prompt"]
        self.assertTrue(repair_prompt.startswith("user"))
        self.assertIn("missing grounded risk claim citations", repair_prompt)
        self.assertEqual(decision["raw_response"], repaired)
        self.assertIn("missing grounded risk", decision["response_diagnostic"]["repair_attempt"]["first_error"])

    def test_second_rejection_fails_with_the_last_answer(self):
        second = answer(grounded_recommendation(article_id=-1))
        with self.assertRaisesRegex(ValueError, "outside the grounded payload") as raised:
            decide(answer(short_of_one_risk_citation()), second)
        self.assertEqual(raised.exception.ask.call_count, 2)
        self.assertEqual(raised.exception.raw_response, second)


class LenientJsonTests(unittest.TestCase):
    def test_raw_control_characters_inside_strings_parse(self):
        self.assertEqual(extract_json_value('{"why_now": "line one\nline two\tend"}'),
                         {"why_now": "line one\nline two\tend"})
        self.assertEqual(extract_json_value('Here you go: {"a": "x\ny"} done'), {"a": "x\ny"})

    def test_text_without_json_still_returns_none(self):
        self.assertIsNone(extract_json_value("no json here"))


if __name__ == "__main__":
    unittest.main()
