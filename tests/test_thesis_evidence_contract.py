"""Phase 4 acceptance: structured theses, deterministic claim checks, handoff and gating.

Offline only: isolated SQLite databases, fixture documents and fake model responses.
"""
import copy
from contextlib import closing
from datetime import datetime, timezone
from functools import partial
import json
import sqlite3
import unittest
from unittest.mock import patch

from tests.research_foundation_fixtures import database, ENTRY
from tests.test_company_evidence import COMPANY, actuals, guidance
from agent_builders.company_evidence_payload import build_company_evidence_payload
from agent_stages.manager_strategist_context import _build_manager_visible_strategist_context
from db_common import get_connection
from market_db_modules.evidence_thesis import get_evidence_thesis
from market_db_modules.evidence_thesis_report import evidence_thesis_report
from services.company_evidence_extraction import ingest_source
from services.company_evidence_packages import build_company_package
from services.company_evidence_policy import CompanyEvidencePolicy
from services.evidence_thesis import (
    check_evidence_thesis, compact_thesis_check, evaluate_evidence_thesis_gate, not_requested_check,
    persist_thesis_check,
)
from services.evidence_thesis_policy import EvidenceThesisPolicy
from services.order_candidates import OrderCandidateBuilder


ARTICLE = {"article_id": 42, "title": "Fixture raises its quarterly outlook",
           "summary": "Fixture said demand for its accelerators remained strong through the quarter."}


def claim(claim_id, statement, *, claim_type="inference", central=False, facts=(), documents=(), articles=(),
          quotes=(), fiscal_period=""):
    return {"claim_id": claim_id, "statement": statement, "claim_type": claim_type, "central": central,
            "fiscal_period": fiscal_period, "fact_ids": list(facts), "document_ids": list(documents),
            "article_ids": list(articles), "quotes": [{"source_id": s, "text": t} for s, t in quotes]}


class ThesisFixture(unittest.TestCase):
    def setUp(self):
        self.db = database(self)
        self.policy = EvidenceThesisPolicy(mode="shadow")

    def package(self, *sources):
        ids = [ingest_source(COMPANY, source, ENTRY, db_path=self.db)[0] for source in sources]
        return build_company_package(COMPANY, ids, ENTRY, CompanyEvidencePolicy(), db_path=self.db)

    def payload(self, *sources):
        package_id = self.package(*(sources or (guidance(80, 90, kind="prior"), guidance(100, 110),
                                               actuals(1234000000))))
        return {"company": {"company_id": 1, "symbol": "NVDA"},
                "company_evidence": build_company_evidence_payload(1, package_id=package_id, as_of=ENTRY,
                                                                   db_path=self.db),
                "supporting_articles": {"article_summaries": [ARTICLE], "full_articles": []},
                "grounding": {"article_ids": [42]}}

    @staticmethod
    def fact(payload, kind, metric="revenue"):
        return next(f["fact_id"] for f in payload["company_evidence"]["facts"]
                    if f["kind"] == kind and f["metric"] == metric)

    def guidance_claim(self, payload, statement="Q3 FY2026 revenue guidance midpoint rose by $20 million, a 23.5% increase",
                       **changes):
        current, prior = self.fact(payload, "guidance"), self.fact(payload, "prior_guidance")
        values = dict(claim_type="calculation", central=True, facts=(current, prior), fiscal_period="FY2026-Q3",
                      quotes=[(current, "current guidance is USD $100 to $110 million")])
        return claim("S1", statement, **{**values, **changes})

    def thesis(self, payload, *, supporting=None, opposing=None, **overrides):
        current = self.fact(payload, "guidance")
        value = {
            "causal_mechanism": "Higher guided revenue indicates stronger accelerator demand this quarter.",
            "change_vs_prior_expectations": {
                "assessment": "supported_comparison", "baseline_type": "prior_guidance",
                "statement": "Guidance midpoint is above prior guidance; market surprise is unknown without consensus.",
                "claim_ids": ["S1"]},
            "direction": "up", "horizon": {"minimum_days": 2, "maximum_days": 20}, "forecast_uncertainty": "medium",
            "supporting_claims": supporting if supporting is not None else [
                self.guidance_claim(payload),
                claim("S2", "Demand commentary supports the higher outlook", articles=[42],
                      quotes=[("article:42", "demand for its accelerators remained strong")])],
            "opposing_claims": opposing if opposing is not None else [
                claim("O1", "Without consensus the size of any market reaction is uncertain", facts=[current])],
            "invalidation_conditions": [{"trigger": "Company lowers FY2026-Q3 revenue guidance",
                                         "data_source": "company_release", "review_within_days": 10}],
            "unresolved_questions": ["Consensus revenue for FY2026-Q3 is unavailable"],
        }
        return {**value, **overrides}

    def check(self, thesis, payload, **kwargs):
        return check_evidence_thesis(thesis, payload, route="legacy_strategist", policy=self.policy,
                                     db_path=self.db, **kwargs)

    def claim_result(self, check, claim_id):
        return next(result for result in check["claims"] if result["claim_id"] == claim_id)


class ClaimCheckTests(ThesisFixture):
    def test_supported_thesis_is_validated_with_resolved_spans(self):
        payload = self.payload()
        check = self.check(self.thesis(payload), payload)
        self.assertEqual((check["status"], check["failures"]), ("validated", []))
        self.assertTrue(self.claim_result(check, "S1")["spans"][0]["end"] > 0)

    def test_valid_article_with_unsupported_number_fails(self):
        payload = self.payload()
        invented = claim("S1", "Fixture expects quarterly revenue of $150 million", claim_type="observed_fact",
                         central=True, articles=[42], quotes=[("article:42", "demand for its accelerators remained strong")])
        check = self.check(self.thesis(payload, supporting=[invented]), payload)
        self.assertIn("unsupported_number:$150 million", self.claim_result(check, "S1")["failures"])

    def test_failed_central_claim_defers(self):
        payload = self.payload()
        invented = self.guidance_claim(payload, "Guidance midpoint rose by $35 million")
        check = self.check(self.thesis(payload, supporting=[invented]), payload)
        self.assertEqual((check["status"], "central_claim_failed" in check["failures"]), ("deferred", True))

    def view_only_payload(self):
        # Article 77 reaches the model only through a view: its scraped title and summary, plus a model-written reason.
        payload = self.payload()
        signal = {"article_id": 77, "title": "Fixture can supply only half of what customers want",
                  "summary": "Fixture can supply only half of what customers want",
                  "reason": "The classifier thinks demand is outstripping supply by a wide margin."}
        return {**payload, "views": {"company_view": {"agent_conclusions": [signal]}},
                "grounding": {"article_ids": [42, 77]}}

    def test_quote_of_a_view_only_article_title_is_verified(self):
        payload = self.view_only_payload()
        headline = claim("S1", "Fixture can supply only half of what customers want", claim_type="observed_fact",
                         central=True, articles=[77],
                         quotes=[("article:77", "Fixture can supply only half of what customers want")])
        result = self.claim_result(self.check(self.thesis(payload, supporting=[headline]), payload), "S1")
        self.assertEqual((result["status"], result["spans"][0]["field"]), ("verified", "title"))

    def test_view_reason_is_not_source_text(self):
        payload = self.view_only_payload()
        paraphrase = claim("S1", "Demand is outstripping supply", claim_type="observed_fact", central=True,
                           articles=[77], quotes=[("article:77", "demand is outstripping supply by a wide margin")])
        result = self.claim_result(self.check(self.thesis(payload, supporting=[paraphrase]), payload), "S1")
        self.assertIn("quote_not_in_source", result["failures"])

    def test_asserted_market_surprise_needs_consensus(self):
        payload = self.payload()
        surprise = self.guidance_claim(payload, "Guidance beat analyst estimates by $20 million")
        check = self.check(self.thesis(payload, supporting=[surprise]), payload)
        self.assertIn("market_surprise_without_consensus", self.claim_result(check, "S1")["failures"])


class CoverageTests(ThesisFixture):
    def test_missing_expectation_baseline_must_be_explicitly_unknown(self):
        payload = self.payload()
        actual = self.fact(payload, "actual")
        change = {"assessment": "supported_comparison", "baseline_type": "prior_guidance",
                  "statement": "Revenue exceeded the prior baseline", "claim_ids": ["S3"]}
        supporting = [self.guidance_claim(payload),
                      claim("S3", "Quarterly revenue was $1.2 billion", claim_type="observed_fact", facts=[actual])]
        check = self.check(self.thesis(payload, supporting=supporting, change_vs_prior_expectations=change), payload)
        self.assertIn("expectation_baseline_not_cited", check["failures"])

    def test_package_opposing_evidence_must_be_addressed(self):
        payload = self.payload(guidance(100, 110, kind="prior"), guidance(80, 90))
        self.assertTrue(payload["company_evidence"]["opposing_claims"])
        article_only = claim("S1", "Demand commentary remains positive", central=True, articles=[42])
        change = {"assessment": "unknown", "baseline_type": "none", "statement": "Not compared", "claim_ids": []}
        check = self.check(self.thesis(payload, supporting=[article_only], opposing=[
            claim("O1", "Macro demand could soften", articles=[42])], change_vs_prior_expectations=change), payload)
        self.assertIn("package_opposing_evidence_unaddressed", check["failures"])

    def test_directional_thesis_needs_opposing_claims_and_invalidation(self):
        payload = self.payload()
        check = self.check(self.thesis(payload, opposing=[], invalidation_conditions=[]), payload)
        self.assertTrue({"no_opposing_claims", "missing_invalidation_condition"} <= set(check["failures"]))

    def test_malformed_thesis_is_invalid_output(self):
        payload = self.payload()
        broken = {key: value for key, value in self.thesis(payload).items() if key != "horizon"}
        self.assertEqual(self.check(broken, payload)["status"], "invalid_output")


class PersistenceAndHandoffTests(ThesisFixture):
    def test_check_is_persisted_immutably(self):
        payload = self.payload()
        check = self.check(self.thesis(payload), payload)
        identity = persist_thesis_check(check, company_id=1, candidate_id="c1", db_path=self.db)
        self.assertEqual(get_evidence_thesis(identity, db_path=self.db)["thesis"], check["thesis"])
        with closing(get_connection(self.db)) as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE evidence_thesis_checks SET status='validated'")

    def manager_payload(self, payload, check):
        recommendation = {"decision": "trade_candidate", "confidence": "high", "summary": "Guidance raised",
                          "evidence_thesis": check["thesis"],
                          "evidence_thesis_check": compact_thesis_check(check, "thesis:legacy:fixture")}
        return {"company": {"symbol": "NVDA"}, "company_evidence": copy.deepcopy(payload["company_evidence"]),
                "strategist_recommendation": recommendation}

    def test_different_package_withholds_thesis(self):
        payload = self.payload()
        manager_payload = self.manager_payload(payload, self.check(self.thesis(payload), payload))
        manager_payload["company_evidence"]["package_id"] = "package:other"
        visible = _build_manager_visible_strategist_context(manager_payload)
        self.assertEqual((visible.get("evidence_thesis"), visible["evidence_thesis_handoff"]), (None, "package_mismatch"))


class ReportTests(ThesisFixture):
    NOW = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)

    def setUp(self):
        super().setUp()
        payload = self.payload()
        invented = self.guidance_claim(payload, "Guidance midpoint rose by $35 million")
        broken = {key: value for key, value in self.thesis(payload).items() if key != "horizon"}
        for thesis in (self.thesis(payload), self.thesis(payload, supporting=[invented]), broken):
            persist_thesis_check(self.check(thesis, payload), company_id=1, db_path=self.db)
        persist_thesis_check(not_requested_check(payload, "no_evidence", route="legacy_strategist", policy=self.policy),
                             company_id=1, db_path=self.db)

    def decision(self, *, would_allow, executed=False, pnl=None, run_at="2026-09-03T10:00:00", other_gates=True):
        output = {"evidence_thesis_gate": {"allowed": True, "would_allow": would_allow, "proposed_decision": "call",
                                           "enforcement_mode": "shadow",
                                           "reason_codes": [] if would_allow else ["thesis_deferred"]},
                  "entry_gate": {"allowed": other_gates}, "market_regime_gate": {"allowed": True},
                  "selected_option": {"symbol": "NVDA260918C00100000"}}
        with closing(get_connection(self.db)) as conn, conn:
            decision_id = conn.execute("""INSERT INTO manager_decision_history
                (decision_run_at,manager_decision,trade_executed,realized_pnl_pct)
                VALUES (?,?,?,?)""", (run_at, "call", int(executed), pnl)).lastrowid
            conn.execute("""INSERT INTO manager_decision_payloads
                (manager_decision_history_id,manager_output_json) VALUES (?,?)""", (decision_id, json.dumps(output)))

    def report(self, **kwargs):
        return evidence_thesis_report(now=self.NOW, db_path=self.db, **kwargs)

    def test_gate_counts_what_enforcement_would_remove(self):
        self.decision(would_allow=True, executed=True, pnl=12.0)
        self.decision(would_allow=False, executed=True, pnl=-30.0)
        self.decision(would_allow=False, other_gates=False)
        gate = self.report()["entry_gate"]
        self.assertEqual((gate["would_block"], gate["would_block_otherwise_eligible"], gate["executed_while_would_block"],
                          gate["descriptive_outcomes"]["would_block"]["mean_realized_pnl_pct"]), (2, 1, 1, -30.0))


class GateTests(unittest.TestCase):
    def recommendation(self, status="validated", direction="up", evaluation_id="thesis:legacy:x"):
        return {"evidence_thesis": {"direction": direction},
                "evidence_thesis_check": {"status": status, "evaluation_id": evaluation_id,
                                          "research_status": "not_investigated"}}

    def gate(self, mode, decision="call", **changes):
        return evaluate_evidence_thesis_gate(self.recommendation(**changes), decision,
                                             policy=EvidenceThesisPolicy(mode=mode))

    def test_shadow_records_would_block_without_blocking(self):
        gate = self.gate("shadow", status="deferred")
        self.assertEqual((gate["allowed"], gate["would_allow"]), (True, False))

    def test_enforce_blocks_deferred_thesis(self):
        self.assertFalse(self.gate("enforce", status="deferred")["allowed"])

    def test_enforce_allows_validated_matching_direction(self):
        self.assertTrue(self.gate("enforce")["allowed"])

    def test_direction_conflict_blocks(self):
        self.assertIn("thesis_direction_conflicts_with_decision", self.gate("enforce", decision="put")["reason_codes"])

    def test_unpersisted_check_blocks(self):
        self.assertIn("thesis_check_not_persisted", self.gate("enforce", evaluation_id=None)["reason_codes"])

    def test_enforce_mode_needs_promotion_proof(self):
        with patch.dict("os.environ", {"EVIDENCE_THESIS_MODE": "enforce"}):
            self.assertEqual(EvidenceThesisPolicy.from_env().mode, "shadow")

    def company_result(self, **gate):
        return {"decision": "call", "confidence": "high", "strategist_recommendation": {"decision": "trade_candidate"},
                "entry_gate": {"allowed": True}, "market_regime_gate": {"allowed": True}, **gate}

    def test_order_builder_rejects_blocked_thesis_gate(self):
        result = self.company_result(evidence_thesis_gate={"allowed": False})
        self.assertFalse(OrderCandidateBuilder()._is_trade_eligible(result))


class StrategistIntegrationTests(ThesisFixture):
    def strategist_payload(self):
        payload = self.payload()
        company = {"company_id": 1, "symbol": "NVDA", "name": "Fixture", "industry_id": 1, "sector_id": 1,
                   "historical_price_data": {}}
        return {**payload, "company": company, "views": {"company_view": {"count": 1}},
                "grounding": {"company": {"id": 1, "symbol": "NVDA"}, "industry": {"id": 1}, "sector": {"id": 1},
                              "article_ids": [42]}}

    def response(self, payload, thesis=True):
        recommendation = {
            "decision": "trade_candidate", "confidence": "high", "evidence_quality": "strong",
            "setup_quality": "strong", "timing_clarity": "clear", "preferred_option_direction": "call",
            "expected_stock_direction": "up", "time_horizon": "short_term", "why_now": "Guidance was raised",
            "summary": "Raised guidance", "catalyst": [], "thesis": [], "risks": [],
            "contradictions_present": False, "watchlist_reason": "", "grounded_company_symbol": "NVDA",
            "grounded_entity_ids": {"company_id": 1, "industry_id": 1, "sector_id": 1},
            "evidence_claims": [{"claim_type": kind, "claim": "Raised guidance", "evidence_article_ids": [42]}
                                for kind in ("summary", "why_now")]}
        if thesis:
            recommendation["evidence_thesis"] = self.thesis(payload)
        return json.dumps({"recommendation": recommendation})

    def run_strategist(self, mode, **response_options):
        from agent_stages import strategist_runner
        payload = self.strategist_payload()
        calls = []

        def ask(**kwargs):
            calls.append(kwargs)
            return self.response(payload, **response_options)

        with patch.object(strategist_runner, "build_strategist_input", return_value=payload), \
                patch.object(strategist_runner, "ask_model", side_effect=ask), \
                patch.object(strategist_runner, "_save_strategist_summary", return_value={"id": 1}), \
                patch.object(strategist_runner, "persist_thesis_check", return_value="thesis:legacy:fixture"), \
                patch.object(strategist_runner, "check_evidence_thesis", partial(check_evidence_thesis, db_path=self.db)):
            result = strategist_runner.decide_company_purchase("NVDA", client=object(),
                                                               thesis_policy=EvidenceThesisPolicy(mode=mode))
        return result["recommendation"], calls[0]

    def test_shadow_attaches_checked_thesis_without_changing_legacy_decision(self):
        recommendation, call = self.run_strategist("shadow")
        self.assertEqual((recommendation["decision"], recommendation["evidence_thesis_check"]["status"],
                          call["include_thesis"]), ("trade_candidate", "validated", True))

    def test_off_mode_keeps_the_legacy_contract(self):
        recommendation, call = self.run_strategist("off", thesis=False)
        self.assertEqual(("evidence_thesis_check" in recommendation, call["include_thesis"]), (False, False))


if __name__ == "__main__":
    unittest.main()
