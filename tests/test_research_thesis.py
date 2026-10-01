"""Phase 4 shadow route: theses over finished research sessions, with budgets and recovery."""
from contextlib import closing
import unittest
from unittest.mock import patch

from tests.test_research_orchestrator import ResearchFixture
from tests.research_foundation_fixtures import ENTRY
from db_common import get_connection
from market_db_modules.evidence_thesis import get_evidence_thesis, reserve_research_thesis
from services.evidence_research_policy import EvidenceResearchPolicy
from services.evidence_thesis_policy import EvidenceThesisPolicy
from services.research_thesis import process_research_theses


class FakeWriter:
    model = "fixture-writer"
    prompt_version = "fixture-thesis-v1"
    identity = {"provider": "fixture"}
    last_response = {}

    def __init__(self, direction="up"):
        self.direction = direction
        self.contexts = []

    def reservation(self, context):
        return {"provider": "ollama", "cost_reserved_usd": 0}

    def write(self, context, *, timeout):
        self.contexts.append(context)
        facts = {fact["kind"]: fact["fact_id"] for fact in context["company_evidence"]["facts"]}
        guided = {"claim_id": "S1", "statement": "Guidance midpoint rose by $20 million", "claim_type": "calculation",
                  "central": True, "fiscal_period": "FY2026-Q3", "fact_ids": [facts["guidance"], facts["prior_guidance"]],
                  "document_ids": [], "article_ids": [], "quotes": []}
        risk = {**guided, "claim_id": "O1", "statement": "Demand could fade", "claim_type": "inference", "central": False,
                "fiscal_period": "", "fact_ids": [facts["guidance"]]}
        return {"evidence_thesis": {
            "causal_mechanism": "Raised guidance implies demand strength.",
            "change_vs_prior_expectations": {"assessment": "supported_comparison", "baseline_type": "prior_guidance",
                                             "statement": "Above prior guidance", "claim_ids": ["S1"]},
            "direction": self.direction, "horizon": {"minimum_days": 1, "maximum_days": 10},
            "forecast_uncertainty": "medium", "supporting_claims": [guided], "opposing_claims": [risk],
            "invalidation_conditions": [{"trigger": "Guidance cut", "data_source": "company_release",
                                         "review_within_days": 5}],
            "unresolved_questions": []}}


class ResearchThesisTests(ResearchFixture):
    def write_theses(self, writer=None, *, thesis_policy=None, research_policy=None):
        return process_research_theses(policy=thesis_policy or EvidenceThesisPolicy(mode="shadow"),
                                       research_policy=research_policy or self.research_policy,
                                       writer=writer or FakeWriter(), clock=lambda: ENTRY, db_path=self.db)

    def ready_session(self):
        self.prior()
        result = self.run_research()
        self.assertEqual(result["status"], "ready")
        return result

    def test_ready_session_gets_one_checked_shadow_thesis(self):
        self.ready_session()
        writer = FakeWriter()
        outcome = self.write_theses(writer)["theses"][0]
        record = get_evidence_thesis(outcome["evaluation_id"], db_path=self.db)
        self.assertEqual((outcome["status"], record["route"], record["research_status"], len(writer.contexts)),
                         ("validated", "research_shadow", "ready", 1))

    def test_thesis_uses_the_final_research_package(self):
        session = self.ready_session()
        writer = FakeWriter()
        self.write_theses(writer)
        self.assertEqual(writer.contexts[0]["company_evidence"]["package_id"], session["final_package_id"])

    def test_sessions_are_not_rewritten(self):
        self.ready_session()
        self.write_theses()
        writer = FakeWriter()
        self.assertEqual((self.write_theses(writer)["theses"], writer.contexts), ([], []))

    def test_not_ready_research_is_recorded_without_a_model_call(self):
        self.assertEqual(self.run_research()["status"], "deferred_missing_evidence")
        writer = FakeWriter()
        outcome = self.write_theses(writer)["theses"][0]
        self.assertEqual((outcome["status"], outcome["failures"], writer.contexts),
                         ("not_requested", ["research_status:deferred_missing_evidence"], []))

    def test_interrupted_call_is_not_retried(self):
        session = self.ready_session()
        reserve_research_thesis(session["session_id"], {"cost_reserved_usd": 0}, at=ENTRY, daily_calls=5,
                                daily_cost_budget=0, db_path=self.db)
        writer = FakeWriter()
        outcome = self.write_theses(writer)["theses"][0]
        self.assertEqual((outcome["status"], writer.contexts), ("failed", []))

    def test_daily_thesis_allowance_is_reserved_before_dispatch(self):
        self.ready_session()
        writer = FakeWriter()
        policy = EvidenceThesisPolicy(mode="shadow", daily_research_thesis_calls=1)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("INSERT INTO research_thesis_allowances VALUES (?,1,0)", (ENTRY[:10],))
        result = self.write_theses(writer, thesis_policy=policy)
        self.assertEqual((result["status"], writer.contexts), ("budget_exhausted:daily_thesis_allowance", []))

    def test_thesis_cost_shares_the_research_daily_budget(self):
        self.ready_session()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("""INSERT INTO research_daily_allowances VALUES (?,'planner',1,1.0)
                ON CONFLICT(day,kind) DO UPDATE SET reserved_cost=1.0""", (ENTRY[:10],))

        class PaidWriter(FakeWriter):
            def reservation(self, context):
                return {"provider": "vertex", "cost_reserved_usd": 0.5}

        research_policy = EvidenceResearchPolicy(mode="shadow", daily_cost_budget=1.2)
        result = self.write_theses(PaidWriter(), research_policy=research_policy)
        self.assertEqual(result["status"], "budget_exhausted:daily_cost_allowance")

    def test_disabled_research_or_off_theses_do_nothing(self):
        self.ready_session()
        off = self.write_theses(thesis_policy=EvidenceThesisPolicy(mode="off"))
        disabled = self.write_theses(research_policy=EvidenceResearchPolicy(mode="disabled"))
        self.assertEqual((off["status"], disabled["status"]), ("disabled", "disabled"))

    def test_result_carries_no_trade_permission(self):
        self.ready_session()
        outcome = self.write_theses()["theses"][0]
        self.assertTrue(outcome["shadow"] and not {"allowed", "selected_option", "decision"} & set(outcome))


class WriterConfigurationTests(unittest.TestCase):
    """The writer follows the strategist/manager configuration convention."""

    def writer(self, **environment):
        from agent_stages.research_thesis_writer import ResearchThesisWriter
        with patch.dict("os.environ", environment, clear=True):
            return ResearchThesisWriter()

    def test_inherits_the_strategist_model_and_host(self):
        writer = self.writer(LLM_PROVIDER="ollama", STRATEGIST_MODEL="strategist-model",
                             STRATEGIST_OLLAMA_HOST="http://ollama:11434")
        self.assertEqual((writer.provider, writer.model, writer.host),
                         ("ollama", "strategist-model", "http://ollama:11434"))

    def test_stage_specific_model_and_host_take_precedence(self):
        writer = self.writer(LLM_PROVIDER="ollama", STRATEGIST_MODEL="strategist-model",
                             RESEARCH_THESIS_MODEL="thesis-model", RESEARCH_THESIS_OLLAMA_HOST="http://thesis:11434")
        self.assertEqual((writer.model, writer.host), ("thesis-model", "http://thesis:11434"))

    def test_provider_comes_from_llm_provider_not_the_planner_override(self):
        writer = self.writer(LLM_PROVIDER="ollama", RESEARCH_PLANNER_PROVIDER="vertex", STRATEGIST_MODEL="m")
        self.assertEqual(writer.provider, "ollama")

    def test_limits_fall_back_to_planner_settings(self):
        writer = self.writer(LLM_PROVIDER="ollama", STRATEGIST_MODEL="m", RESEARCH_PLANNER_MAX_OUTPUT_TOKENS="2048",
                             RESEARCH_THESIS_THINKING_BUDGET="512")
        self.assertEqual((writer.policy.max_output_tokens, writer.policy.thinking_budget), (2048, 512))

    def test_paid_model_different_from_planner_needs_its_own_rates(self):
        with self.assertRaisesRegex(ValueError, "RESEARCH_THESIS_INPUT/OUTPUT_USD_PER_MILLION"):
            self.writer(LLM_PROVIDER="vertex", STRATEGIST_MODEL="gemini-2.5-flash",
                        RESEARCH_THESIS_MODEL="gemini-2.5-pro", RESEARCH_PLANNER_INPUT_USD_PER_MILLION="0.3",
                        RESEARCH_PLANNER_OUTPUT_USD_PER_MILLION="2.5")

    def test_paid_model_with_own_rates_uses_them(self):
        writer = self.writer(LLM_PROVIDER="vertex", STRATEGIST_MODEL="gemini-2.5-flash",
                             RESEARCH_THESIS_MODEL="gemini-2.5-pro", RESEARCH_THESIS_INPUT_USD_PER_MILLION="1.25",
                             RESEARCH_THESIS_OUTPUT_USD_PER_MILLION="10")
        self.assertEqual((writer.policy.input_usd_per_million, writer.policy.output_usd_per_million), (1.25, 10.0))

    def test_same_paid_model_as_planner_reuses_planner_rates(self):
        writer = self.writer(LLM_PROVIDER="vertex", STRATEGIST_MODEL="gemini-2.5-flash",
                             RESEARCH_PLANNER_INPUT_USD_PER_MILLION="0.3", RESEARCH_PLANNER_OUTPUT_USD_PER_MILLION="2.5")
        self.assertEqual(writer.policy.input_usd_per_million, 0.3)


if __name__ == "__main__":
    unittest.main()
