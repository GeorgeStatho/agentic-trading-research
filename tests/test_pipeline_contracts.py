"""Pipeline contracts: model schemas, prompt examples, typed results and the gate registry agree.

A field added to a schema but not to its prompt example or its TypedDict, or a gate added
to the registry but not carried to orders, fails here instead of drifting silently.
"""
import copy
import json
import unittest

from agent_stages.manager_normalization import normalize_recommendation as normalize_manager
from agent_stages.manager_prompt import build_manager_prompt
from agent_stages.manager_model import MANAGER_RECOMMENDATION_SCHEMA
from agent_stages.strategist_prompt import build_strategist_prompt
from agent_stages.strategist_schema import strategist_recommendation_schema
from agentCallers.decision_pipeline import _build_selected_option_output
from services.gates.entry_gates import ENTRY_GATES, entry_allowed
from services.trading.order_candidates import OrderCandidateBuilder


def typed_keys(typed_dict):
    return typed_dict.__required_keys__ | typed_dict.__optional_keys__


def schema_fields(schema):
    return set(schema["properties"]["recommendation"]["properties"])


def prompt_example(system_and_user_prompt):
    return set(json.loads(system_and_user_prompt[1])["required_output"]["recommendation"])


STRATEGIST_PAYLOAD = {"company": {"symbol": "NVDA", "historical_price_data": {}}, "supporting_articles": {}}
COMPACT_PAYLOAD = {
    "company": {"symbol": "NVDA", "historical_price_data": {}},
    "grounding": {"article_ids": [101, 102, 103]},
    "views": {"company": {"count": 1, "agent_conclusions": [{"article_id": 101, "source_url": "http://x/1", "note": "n"}]}},
    "supporting_articles": {
        "article_summaries": [{"article_id": 101, "agent_signals": {"a": 1}, "source_url": "http://x/1"},
                              {"article_id": 102, "agent_signals": {"a": 2}, "source_url": "http://x/2"}],
        "full_articles": [{"article_id": 101, "agent_signals": {"a": 1}, "source_url": "http://x/1", "body": "text"},
                          {"article_id": 103, "agent_signals": {}, "source_url": "http://x/3"}],
    },
    "company_evidence": {"documents": [{"document_id": "d1"}], "facts": [{
        "fact_id": "f1", "document_id": "d1", "source_excerpt": "Revenue rose", "superseded": False, "count": 0,
        "company_id": 7, "schema_version": 2, "extraction_version": 3, "source_start": 1, "source_end": 9,
        "unit": None, "tags": [], "meta": {}, "note": ""}]},
}


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

    def test_compact_prompt_keeps_grounding_and_drops_noise(self):
        before = copy.deepcopy(COMPACT_PAYLOAD)
        user = build_strategist_prompt(COMPACT_PAYLOAD, include_thesis=True)[1]
        self.assertEqual(COMPACT_PAYLOAD, before)
        for dropped in ("agent_signals", "source_url"):
            self.assertNotIn(dropped, user)
        payload = json.loads(user)
        self.assertEqual(payload["grounding"], COMPACT_PAYLOAD["grounding"])
        self.assertEqual({a["article_id"] for section in payload["supporting_articles"].values() for a in section}, {101, 102, 103})
        supporting = payload["supporting_articles"]
        self.assertEqual([a["article_id"] for a in supporting["article_summaries"]], [102])
        self.assertEqual([a["article_id"] for a in supporting["full_articles"]], [101, 103])

    def test_compact_facts_keep_excerpt_and_falsy_values(self):
        fact = json.loads(build_strategist_prompt(COMPACT_PAYLOAD)[1])["company_evidence"]["facts"][0]
        self.assertEqual(fact, {"fact_id": "f1", "document_id": "d1", "source_excerpt": "Revenue rose",
                                "superseded": False, "count": 0})


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
