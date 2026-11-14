"""The gap-closing features inside one real worker cycle, with no model calls.

Each test runs a whole trading cycle (tests/e2e/test_trading_cycle.py harness): the strategist and
manager answer from scripted replies, so what is checked is everything around them. That means the
prompts they are sent, the gates that judge their answers, the order that results, what the
database records, and what research and the dashboard read back. Run with:

    python3 scripts/project.py test --e2e
"""
from __future__ import annotations

import json
import os
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest import mock

from db_common import get_connection
from market_db_modules.compact_storage import snapshot_text
from tests.e2e.test_trading_cycle import E2E_ONLY, TradingCycleHarness
from tests.integration.fakes import ARTICLE_IDS, ScriptedModel, bearish_news, manager_reply, strategist_reply

NEW_GATES = ("TRADABILITY", "ENTRY_EXTENSION", "REGIME_SPY_TREND", "TRADE_ECONOMICS")
# A format placeholder left in a prompt, such as "{symbol}", means a template was never filled.
PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def _strict_json(text):
    """Prompts are JSON documents; NaN and Infinity are not JSON and a model may misread them."""
    def refuse(constant):
        raise ValueError(f"non-JSON constant {constant} in a prompt")
    return json.loads(text, parse_constant=refuse)


def _proof_for(*stages):
    """A promotion proof exists for exactly these stages; every other gate stays in shadow."""
    return mock.patch("services.research.research_promotion.approved",
                      side_effect=lambda stage, *args, **kwargs: stage in stages)


@E2E_ONLY
class GapFeaturesEndToEndTests(TradingCycleHarness):
    def snapshots(self, stage):
        with closing(get_connection()) as conn:
            return [json.loads(snapshot_text(row, conn))["payload"]
                    for row in conn.execute("SELECT * FROM candidate_snapshots WHERE stage=?", (stage,))]

    def rejections(self):
        with closing(get_connection()) as conn:
            return {(row["stage"], row["reason_code"], row["shadow"])
                    for row in conn.execute("SELECT stage, reason_code, shadow FROM candidate_rejections")}

    def prompts(self, model, stage):
        return ([prompt for asked, prompt in model.system_prompts if asked == stage],
                [prompt for asked, prompt in model.prompts if asked == stage])

    def cited_families(self, payload):
        """The stored family of every catalyst the prompt's articles cite; the prompt carries only the id."""
        articles = payload["supporting_articles"]["full_articles"] + payload["supporting_articles"].get("article_summaries", [])
        ids = {article["catalyst_id"] for article in articles}
        self.assertTrue(articles and ids)
        with closing(get_connection()) as conn:
            rows = [conn.execute("SELECT catalyst_family, classification_version FROM catalysts WHERE catalyst_id=?",
                                 (catalyst_id,)).fetchone() for catalyst_id in ids]
        self.assertNotIn(None, rows)
        return {(row["catalyst_family"], row["classification_version"]) for row in rows}

    def test_both_prompts_are_clean_json_and_carry_the_new_scope_and_catalyst(self):
        from agent_stages.manager_model import MANAGER_PROMPT_VERSION
        from agent_stages.strategist_prompt import STRATEGIST_PROMPT_VERSION
        from catalyst_models import CLASSIFICATION_VERSION

        model = ScriptedModel(strategist=strategist_reply(), manager=manager_reply("call"))
        self.run_cycle(model)

        for stage, keys in (("strategist", {"task", "company", "supporting_articles", "market_context",
                                            "grounding", "required_output"}),
                            ("manager", {"task", "company", "supporting_articles", "strategist_recommendation",
                                         "market_context", "required_output"})):
            with self.subTest(stage=stage):
                (system,), (user,) = self.prompts(model, stage)
                payload = _strict_json(user)
                self.assertLessEqual(keys, set(payload))
                self.assertIn("negative company-specific catalyst", system)
                self.assertIsNone(PLACEHOLDER.search(system), system)
                if stage == "strategist":
                    self.assertEqual(self.cited_families(payload), {("guidance_raise", CLASSIFICATION_VERSION)})
                else:
                    # The manager sees summaries of the same seeded articles and the strategist's answer.
                    self.assertEqual({article["article_id"] for article in payload["supporting_articles"]["article_summaries"]},
                                     set(ARTICLE_IDS))
                    self.assertEqual(payload["strategist_recommendation"]["decision"], "trade_candidate")
        with closing(get_connection()) as conn:
            strategist_version = conn.execute("SELECT prompt_version FROM strategist_runs").fetchone()[0]
            manifest = json.loads(conn.execute("SELECT pipeline_version_json FROM manager_decision_history").fetchone()[0])
        self.assertEqual(strategist_version, STRATEGIST_PROMPT_VERSION)
        self.assertEqual((manifest["strategist_prompt_version"], manifest["manager_prompt_version"]),
                         (STRATEGIST_PROMPT_VERSION, MANAGER_PROMPT_VERSION))

    def test_every_new_gate_records_a_shadow_verdict_and_the_order_still_goes_out(self):
        model = ScriptedModel(strategist=strategist_reply(), manager=manager_reply("call"))
        result = self.run_cycle(model)

        self.assertEqual(result["trade_result"]["submitted_count"], 1)
        for stage in NEW_GATES:
            with self.subTest(stage=stage):
                verdict, = self.snapshots(stage)
                self.assertTrue(verdict["allowed"])
                self.assertEqual(verdict.get("enforcement_mode", verdict.get("mode")), "shadow")
        # No priced scenarios exist in a fresh database, so economics is unknown, not a rejection.
        economics, = self.snapshots("TRADE_ECONOMICS")
        self.assertEqual((economics["would_allow"], economics["outcome"]), (None, "unavailable"))
        self.assertNotIn("TRADE_ECONOMICS", {stage for stage, _, _ in self.rejections()})
        score, = self.snapshots("pick_score")
        self.assertEqual((score["version"], score["use"]), ("pick-score-v1", "log_only"))
        self.assertIsNotNone(score["score"])
        with closing(get_connection()) as conn:
            stored = conn.execute("SELECT pick_score, pick_score_version FROM manager_decision_history").fetchone()
        self.assertEqual((stored["pick_score"], stored["pick_score_version"]), (score["score"], "pick-score-v1"))

    def test_a_negative_catalyst_reaches_both_prompts_and_becomes_a_put_order(self):
        bearish_news()
        model = ScriptedModel(strategist=strategist_reply(direction="put"), manager=manager_reply("put"))
        result = self.run_cycle(model)

        _, (user,) = self.prompts(model, "strategist")
        self.assertEqual({family for family, _ in self.cited_families(_strict_json(user))}, {"guidance_cut"})
        self.assertEqual([stage for stage, _ in model.prompts], ["strategist", "manager"])
        self.assertEqual(result["trade_result"]["submitted_count"], 1)
        order, = self.market.orders
        self.assertEqual(order.symbol[len("NVDA") + 6], "P", order.symbol)

    def test_an_enforced_tradability_rejection_skips_both_model_calls(self):
        self.enterContext(mock.patch.dict(os.environ, {"TRADABILITY_MODE": "enforce",
                                                       "MAX_PREMIUM_PER_TRADE_DOLLARS": "1"}))
        self.enterContext(_proof_for("TRADABILITY"))
        model = ScriptedModel(strategist=strategist_reply(), manager=manager_reply("call"))
        result = self.run_cycle(model)

        self.assertEqual((model.prompts, self.market.orders, result["trade_result"]["submitted_count"]), ([], [], 0))
        verdict, = self.snapshots("TRADABILITY")
        self.assertEqual((verdict["allowed"], verdict["enforcement_mode"]), (False, "enforce"))
        self.assertIn(("TRADABILITY", 0), {(stage, shadow) for stage, _, shadow in self.rejections()})

    def test_an_enforced_spy_trend_variant_blocks_a_put_while_spy_rises(self):
        bearish_news()
        self.enterContext(mock.patch.dict(os.environ, {"REGIME_SPY_TREND_MODE": "enforce"}))
        self.enterContext(_proof_for("REGIME_SPY_TREND"))
        model = ScriptedModel(strategist=strategist_reply(direction="put"), manager=manager_reply("put"))
        result = self.run_cycle(model)

        verdict, = self.snapshots("REGIME_SPY_TREND")
        self.assertGreater(verdict["spy_return_5d_pct"], 0)
        self.assertGreater(verdict["spy_return_20d_pct"], 0)
        self.assertEqual((verdict["would_allow"], verdict["allowed"], verdict["enforcement_mode"]),
                         (False, False, "enforce"))
        self.assertEqual((self.market.orders, result["trade_result"]["submitted_count"]), ([], 0))
        self.assertIn(("REGIME_SPY_TREND", "spy_5d_and_20d_up_blocks_put", 0), self.rejections())

    def test_the_spy_trend_variant_yields_to_an_enforcing_regime_gate(self):
        self.enterContext(mock.patch.dict(os.environ, {"REGIME_SPY_TREND_MODE": "enforce",
                                                       "MARKET_REGIME_GATE_MODE": "enforce"}))
        self.enterContext(_proof_for("REGIME_SPY_TREND", "REGIME"))
        with self.assertLogs("services.gates.market_regime_variants", level="WARNING"):
            self.run_cycle(ScriptedModel(strategist=strategist_reply(), manager=manager_reply("call")))
        verdict, = self.snapshots("REGIME_SPY_TREND")
        self.assertEqual(verdict["enforcement_mode"], "shadow")

    def test_research_enrols_the_cycle_with_every_new_gate_and_pools_it(self):
        from market_db_modules.research_reports import pooled_gate_report
        from market_db_modules.research_runs import collect_active_runs

        self.run_cycle(ScriptedModel(strategist=strategist_reply(), manager=manager_reply("call")))
        # A candidate under five minutes old is left for the next collection, so collect a little later.
        collected = collect_active_runs(as_of=datetime.now(timezone.utc) + timedelta(minutes=10))
        self.assertEqual([run["candidates"] for run in collected], [1], collected)
        with closing(get_connection()) as conn:
            inputs = [json.loads(row["input_json"]) for row in conn.execute("SELECT input_json FROM research_candidates")]
        enrolled, = inputs
        self.assertLessEqual({"TRADABILITY", "REGIME_SPY_TREND"}, set(enrolled["features"]["gates"]))
        self.assertLessEqual(set(NEW_GATES), set(enrolled["gate_code"]))
        # Once a whole holding period has passed the row is inside the pooling window.
        later = datetime.now(timezone.utc) + timedelta(days=8)
        for stage in ("TRADABILITY", "REGIME_SPY_TREND", "TRADE_ECONOMICS"):
            with self.subTest(stage=stage):
                gate, = pooled_gate_report(stage, as_of=later)["gates"]
                self.assertEqual(gate["n_seen"], 0 if stage == "TRADE_ECONOMICS" else 1)

    def test_the_dashboard_reports_the_new_gates_with_their_modes_and_activity(self):
        self.run_cycle(ScriptedModel(strategist=strategist_reply(), manager=manager_reply("call")))
        status = self.get(self.api(), "/api/v2-status")
        features = {feature["key"]: feature for feature in status["features"]}
        for key in ("entry_extension", "tradability", "regime_spy_trend"):
            with self.subTest(feature=key):
                self.assertEqual(features[key]["effective_mode"], "shadow")
        self.assertNotIn("regime_spy_trend", {feature["key"] for feature in status["shadow_only_features"]})
        decisions = self.get(self.api(), "/api/company-decisions")["companies"]
        self.assertEqual([(row["symbol"], row["manager"]["decision"]) for row in decisions], [("NVDA", "call")])
