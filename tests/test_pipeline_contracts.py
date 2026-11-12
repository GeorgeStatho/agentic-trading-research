"""Pipeline contracts: model schemas, prompt examples, typed results and the gate registry agree.

A field added to a schema but not to its prompt example or its TypedDict, or a gate added
to the registry but not carried to orders, fails here instead of drifting silently.
"""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest
from unittest.mock import patch

from agent_builders import company_evidence_payload, strategist_payload

from agent_stages.manager_normalization import normalize_recommendation as normalize_manager
from agent_stages.manager_prompt import build_manager_prompt
from agent_stages.manager_model import MANAGER_PROMPT_VERSION, MANAGER_RECOMMENDATION_SCHEMA
from agent_stages import research_thesis_writer
from agent_stages.strategist_prompt import (
    LEGACY_STRATEGIST_PROMPT_VERSION, STRATEGIST_PROMPT_VERSION, build_strategist_prompt,
)
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


PLACEHOLDER_MECHANISM = "Unclassified transmission mechanism; review required"
ROLLUP = {"impact_count": 3, "positive_count": 2, "negative_count": 1, "mixed_count": 0, "neutral_count": 0,
          "high_materiality_count": 1, "medium_materiality_count": 2, "low_materiality_count": 0,
          "immediate_count": 0, "short_term_count": 3, "medium_term_count": 0, "unclear_count": 0,
          "direct_count": 2, "indirect_count": 1, "high_materiality_direct_count": 1,
          "direction_counts": {"positive": 2, "negative": 1}, "dominant_direction": "positive",
          "top_reasons": [{"reason": "r"}], "top_direct_reasons": [{"reason": "r"}]}
PRUNE_PAYLOAD = {
    "as_of": "2026-11-09T20:00:00+00:00",
    "company": {"symbol": "ANET",
                "market_data": {"previous_close": 178.89, "regular_market_previous_close": 177.24,
                                "market weight": 0.2, "market_weight": 0.2},
                "historical_price_data": {"1d": {"available": True, "symbol": "ANET", "error": None,
                                                 "percent_change": 3.75, "recent_points": [{"close": 187.5}]}}},
    "peer_groups": {"top_companies": [{"symbol": "DELL"}]},
    "filters": {"max_age_days": 5},
    "opportunist_rollup": {"company": dict(ROLLUP), "industries": dict(ROLLUP), "sector": dict(ROLLUP)},
    "grounding": {"article_ids": [1, 2]},
    "views": {"company_view": {"count": 2, "agent_conclusions": [
        {"article_id": 1, "title": "t", "reason": "why", "summary": "", "evidence_score": 0.6823691479845848,
         "impact_direction": "positive", "time_horizon": "short_term", "catalyst_type": "earnings",
         "mechanism": PLACEHOLDER_MECHANISM, "catalyst_id": "catalyst:abc", "root_event_ids": ["root:x"],
         "catalyst_family": "company", "expected_horizon": "1_3_days", "company_name": "Arista Networks, Inc."},
        {"article_id": 2, "title": "u", "reason": "why", "summary": "real summary", "evidence_score": 0.7,
         "mechanism": "Reported performance changes earnings expectations", "industry_name": "Hardware",
         "sector_name": "Technology"}]}},
    "supporting_articles": {"article_summaries": [], "full_articles": []},
    "company_evidence": {
        "package_id": "package:p", "catalyst_id": "catalyst:c", "event_revision": "rev", "schema_version": "v1",
        "fact_ids": ["f1", "f2"], "document_ids": ["d1"], "gaps": ["g"], "coverage_status": "partial",
        "facts": [{"fact_id": "f1", "document_id": "d1", "value": 1, "excerpt_truncated": False,
                   "source_excerpt": '{"cik":"0001596532","item":{"val":3657600000}}'},
                  {"fact_id": "f2", "document_id": "d1", "value": 2, "source_excerpt": "Revenue rose"}],
        "documents": [{"document_id": "d1", "document_type": "regulatory_filing", "published_at": "2026-11-03",
                       "first_observed_at": "x", "ingested_at": "y",
                       "metadata": {"form": "10-Q", "filing_date": "2026-11-03", "report_date": "2026-09-30",
                                    "raw_response_id": "response:r", "cik": "1", "event_key": "e", "gaps": []}}]},
}


class StrategistPromptPruningTests(unittest.TestCase):
    """2026-11-09 audit: about a third of the prompt was pipeline bookkeeping, duplicates or text the model can't use.
    The stored payload stays complete; only the prompt drops these."""

    def prompt(self):
        before = copy.deepcopy(PRUNE_PAYLOAD)
        system, user = build_strategist_prompt(PRUNE_PAYLOAD, include_thesis=True)
        self.assertEqual(PRUNE_PAYLOAD, before)
        return system, json.loads(user)

    def test_sections_without_model_value_are_dropped(self):
        _, user = self.prompt()
        self.assertNotIn("filters", user)
        self.assertNotIn("peer_groups", user)
        self.assertEqual(user["as_of"], "2026-11-09T20:00:00+00:00")
        self.assertEqual(user["company"]["market_data"], {"previous_close": 178.89, "market_weight": 0.2})
        self.assertEqual(user["company"]["historical_price_data"], {"1d": {"percent_change": 3.75}})

    def test_company_evidence_keeps_facts_and_drops_bookkeeping(self):
        _, user = self.prompt()
        evidence = user["company_evidence"]
        for key in ("package_id", "catalyst_id", "event_revision", "schema_version", "fact_ids", "document_ids"):
            self.assertNotIn(key, evidence)
        self.assertEqual((evidence["gaps"], evidence["coverage_status"]), (["g"], "partial"))
        self.assertEqual(evidence["facts"], [{"fact_id": "f1", "document_id": "d1", "value": 1},
                                             {"fact_id": "f2", "document_id": "d1", "value": 2,
                                              "source_excerpt": "Revenue rose"}])
        self.assertEqual(evidence["documents"], [{
            "document_id": "d1", "document_type": "regulatory_filing", "published_at": "2026-11-03",
            "metadata": {"form": "10-Q", "filing_date": "2026-11-03", "report_date": "2026-09-30"}}])

    def test_classifier_conclusions_keep_signals_and_drop_ids_and_placeholders(self):
        _, user = self.prompt()
        first, second = user["views"]["company_view"]["agent_conclusions"]
        self.assertEqual(first, {"article_id": 1, "title": "t", "reason": "why", "evidence_score": 0.68,
                                 "impact_direction": "positive", "time_horizon": "short_term",
                                 "catalyst_type": "earnings"})
        self.assertEqual(second, {"article_id": 2, "title": "u", "reason": "why", "summary": "real summary",
                                  "evidence_score": 0.7,
                                  "mechanism": "Reported performance changes earnings expectations"})

    def test_rollup_drops_counts_already_in_the_count_dicts(self):
        _, user = self.prompt()
        for level in ("company", "industries", "sector"):
            self.assertEqual(user["opportunist_rollup"][level], {
                "impact_count": 3, "high_materiality_direct_count": 1, "direction_counts": {"positive": 2, "negative": 1},
                "dominant_direction": "positive", "top_reasons": [{"reason": "r"}],
                "top_direct_reasons": [{"reason": "r"}]})

    def test_no_instructions_for_technical_indicators_the_strategist_never_gets(self):
        system, user = self.prompt()
        for text in ("RSI", "MACD", "VWAP", "technical indicators"):
            self.assertNotIn(text, system + user["task"])


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


def fingerprint(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


class PromptVersionTests(unittest.TestCase):
    """Replay caches reuse an answer for its prompt label, so a label must name exactly one rendered prompt.

    PRUNE_PAYLOAD reaches every compaction rule, so a change to the text, the compaction or the response
    schema changes a fingerprint here. Bump the label and pin the new fingerprint together.
    """
    FINGERPRINTS = {
        "grounded-strategist-v11-bearish-scope": "1752ea5531d2ec00bc978142dd1d3cfd3d4d0267a7b566e4ca81ed04d92086c6",
        "grounded-strategist-v5-compact-evidence-bearish-scope": "22e6bca1c3de8c02ca7d0f723097594e4600834ee662a220b6d1b366eaac208b",
        "manager-prompt-v7-bearish-scope": "46a15b372d5d6957177d42db70d1d251a1edf5dfe9cd0d154a25db22bed8c3de",
        "research-thesis-v3": "c074e6e94a4fe5a2c01657a9ec7bb6ab41bbb3935ff483e4b14e67e197c70f0e",
    }

    def rendered(self) -> dict[str, str]:
        return {
            STRATEGIST_PROMPT_VERSION: fingerprint(*build_strategist_prompt(PRUNE_PAYLOAD, include_thesis=True),
                                                   strategist_recommendation_schema(include_thesis=True)),
            LEGACY_STRATEGIST_PROMPT_VERSION: fingerprint(*build_strategist_prompt(PRUNE_PAYLOAD, include_thesis=False),
                                                          strategist_recommendation_schema(include_thesis=False)),
            MANAGER_PROMPT_VERSION: fingerprint(*build_manager_prompt({"company": {"symbol": "NVDA"}}),
                                                MANAGER_RECOMMENDATION_SCHEMA),
            research_thesis_writer.PROMPT_VERSION: fingerprint(research_thesis_writer.SYSTEM_PROMPT,
                                                               research_thesis_writer.RESPONSE_SCHEMA),
        }

    def test_prompts_put_negative_company_catalysts_in_scope(self):
        strategist = build_strategist_prompt(PRUNE_PAYLOAD, include_thesis=True)[0]
        manager = build_manager_prompt({"company": {"symbol": "NVDA"}})[0]
        self.assertNotIn("mostly negative", strategist)
        for name, text in (("strategist", strategist), ("manager", manager)):
            with self.subTest(name):
                self.assertIn("negative company-specific catalyst", text)
                self.assertIn("same evidence bar", text)

    def test_each_prompt_label_names_one_rendered_prompt(self):
        for label, rendered in self.rendered().items():
            with self.subTest(label):
                self.assertEqual(self.FINGERPRINTS.get(label), rendered,
                                 f"prompt changed: bump {label} and pin its fingerprint {rendered}")


class StrategistInputAsOfTests(unittest.TestCase):
    """The decision time the prompt shows comes from the payload builder, not from the prompt."""

    def build(self, end_time):
        evidence = {"company": {}, "peer_groups": [], "macro_impacts": [], "sector_impacts": [],
                    "industry_impacts": [], "company_impacts": []}
        with patch.multiple(strategist_payload, initialize_news_database=lambda: None,
                            build_strategist_evidence_sections=lambda *a, **k: evidence,
                            _build_opportunist_rollup=lambda e: {}, _build_view=lambda **k: {},
                            _serialize_company_scope=lambda c: {"company_id": 1},
                            _build_supporting_articles=lambda *a, **k: {},
                            build_strategist_grounding=lambda p: {}), \
                patch.object(company_evidence_payload, "build_company_evidence_payload", lambda *a, **k: {}):
            return strategist_payload.build_strategist_input("NVDA", end_time=end_time)

    def test_a_replay_carries_its_end_time(self):
        end = datetime(2026, 11, 9, 15, tzinfo=timezone(timedelta(hours=-5)))
        self.assertEqual(self.build(end)["as_of"], "2026-11-09T20:00:00+00:00")

    def test_a_live_decision_carries_the_current_time(self):
        as_of = datetime.fromisoformat(self.build(None)["as_of"])
        self.assertLess(abs(datetime.now(timezone.utc) - as_of), timedelta(minutes=1))


if __name__ == "__main__":
    unittest.main()
