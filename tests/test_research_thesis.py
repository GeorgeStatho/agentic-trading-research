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


class WriterConfigurationTests(unittest.TestCase):
    """The writer follows the strategist/manager configuration convention."""

    def writer(self, **environment):
        from agent_stages.research_thesis_writer import ResearchThesisWriter
        with patch.dict("os.environ", environment, clear=True):
            return ResearchThesisWriter()

    def test_paid_model_different_from_planner_needs_its_own_rates(self):
        with self.assertRaisesRegex(ValueError, "RESEARCH_THESIS_INPUT/OUTPUT_USD_PER_MILLION"):
            self.writer(LLM_PROVIDER="vertex", STRATEGIST_MODEL="gemini-2.5-flash",
                        RESEARCH_THESIS_MODEL="gemini-2.5-pro", RESEARCH_PLANNER_INPUT_USD_PER_MILLION="0.3",
                        RESEARCH_PLANNER_OUTPUT_USD_PER_MILLION="2.5")


if __name__ == "__main__":
    unittest.main()
