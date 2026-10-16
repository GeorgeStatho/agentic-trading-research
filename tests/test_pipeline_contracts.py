"""Pipeline contracts: model schemas, prompt examples, typed results and the gate registry agree.

A field added to a schema but not to its prompt example or its TypedDict, or a gate added
to the registry but not carried to orders, fails here instead of drifting silently.
"""
import json
import unittest

from agent_stages.manager_normalization import normalize_recommendation as normalize_manager
from agent_stages.manager_prompt import build_manager_prompt
from agent_stages.manager_shared import MANAGER_RECOMMENDATION_SCHEMA
from agent_stages.strategist_prompt import build_strategist_prompt
from agent_stages.strategist_schema import strategist_recommendation_schema
from agentCallers.main import _build_selected_option_output
from services.entry_gates import ENTRY_GATES, entry_allowed
from services.order_candidates import OrderCandidateBuilder


def typed_keys(typed_dict):
    return typed_dict.__required_keys__ | typed_dict.__optional_keys__


def schema_fields(schema):
    return set(schema["properties"]["recommendation"]["properties"])


def prompt_example(system_and_user_prompt):
    return set(json.loads(system_and_user_prompt[1])["required_output"]["recommendation"])


STRATEGIST_PAYLOAD = {"company": {"symbol": "NVDA", "historical_price_data": {}}, "supporting_articles": {}}
MANAGER_RESPONSE = {"decision": "call", "confidence": "high", "thesis_confidence": "high", "timing_confidence": "medium",
                    "execution_confidence": "low", "target_dte_bucket": "20_30", "technical_confirmation": "strong",
                    "entry_quality": "good", "recommended_action_adjustment": "approve", "overextension_risk": "low",
                    "options_pricing_risk": "medium", "technical_summary": "Trend confirms",
                    "risk_summary": "Moderate IV", "reason": "Guidance raised"}


class ManagerContractTests(unittest.TestCase):
    def test_prompt_example_matches_schema(self):
        self.assertEqual(prompt_example(build_manager_prompt({"company": {"symbol": "NVDA"}})),
                         schema_fields(MANAGER_RECOMMENDATION_SCHEMA))

    def test_requested_confidence_components_survive_normalization(self):
        normalized = normalize_manager(MANAGER_RESPONSE)
        self.assertEqual([normalized[key] for key in ("thesis_confidence", "timing_confidence", "execution_confidence")],
                         ["high", "medium", "low"])


class StrategistContractTests(unittest.TestCase):
    def test_prompt_example_matches_schema_with_thesis(self):
        self.assertEqual(prompt_example(build_strategist_prompt(STRATEGIST_PAYLOAD, include_thesis=True)),
                         schema_fields(strategist_recommendation_schema(include_thesis=True)))

    def test_strategist_schema_has_no_max_items(self):
        # Sent as Vertex response_schema, gemini-3.8-flash returns 400 INVALID_ARGUMENT for the thesis
        # schema when maxItems is present. response_json_schema (planner transport) accepts it.
        self.assertNotIn('"maxItems"', json.dumps(strategist_recommendation_schema(include_thesis=True)))


class GateRegistryTests(unittest.TestCase):
    RESULT = {"company": {"symbol": "NVDA"}, "recommendation": {"decision": "call", "confidence": "high"},
              "selected_option": {"symbol": "NVDA260918C00100000"},
              **{gate.result_key: {"allowed": True} for gate in ENTRY_GATES}}

    def test_every_gate_reaches_selected_option_output(self):
        company = _build_selected_option_output(ran_at="2026-09-30", manager_results=[self.RESULT])["companies"][0]
        self.assertLessEqual({gate.result_key for gate in ENTRY_GATES}, set(company))

    def test_every_gate_reaches_order_candidates(self):
        candidate = OrderCandidateBuilder().build_from_manager_result(self.RESULT)
        self.assertLessEqual({gate.result_key for gate in ENTRY_GATES}, set(candidate))

    def test_any_blocking_gate_blocks_entry(self):
        for gate in ENTRY_GATES:
            with self.subTest(gate=gate.name):
                self.assertFalse(entry_allowed({**self.RESULT, gate.result_key: {"allowed": False}}))

    def test_missing_required_gate_blocks_entry(self):
        required = next(gate for gate in ENTRY_GATES if gate.verdict_required)
        result = {key: value for key, value in self.RESULT.items() if key != required.result_key}
        self.assertFalse(entry_allowed(result))


if __name__ == "__main__":
    unittest.main()
