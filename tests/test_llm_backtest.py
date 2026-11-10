"""LLM replay backtests: model pricing, answer caching and the replay funnel, with no model server."""
from __future__ import annotations

from contextlib import closing
import json
from dataclasses import replace
from datetime import datetime, timedelta
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from db_common import get_connection
from news_db_modules.bootstrap import initialize_database as initialize_news_database
from market_db_modules.entities import add_company, add_industry, add_sector
from market_db_modules.bootstrap import initialize_database
from market_db_modules.backtesting import (
    record_historical_option_quote,
    record_historical_underlying_bar,
)
from market_db_modules.backtesting import run_decision_backtest
from market_db_modules.candidate_lifecycle import record_candidate_snapshot
from services.evaluation import llm_backtest
from services.core.trading_config import WorkerSettings
from services.thesis.evidence_thesis_policy import EvidenceThesisPolicy
from services.evaluation.llm_backtest import ModelUnavailable, ProductionRules, run_llm_backtest
from services.trading.order_candidates import OrderCandidateBuilder

DATA_DIR = Path(__file__).resolve().parents[1] / "Data"
DECISION_AT = "2026-03-02T15:00:00+00:00"
CONTRACT = {"symbol": "MSFT260417C00400000", "contract_type": "call", "strike_price": 400.0,
            "expiration_date": "2026-04-17", "latest_quote": {"bid_price": 9.8, "ask_price": 10.2}}
TRADEABLE = {"recommendation": {"decision": "call", "confidence": "high", "reason": "fixture"},
             "strategist_recommendation": {"decision": "trade_candidate"},
             "entry_gate": {"allowed": True}, "market_regime_gate": {"allowed": True},
             "selected_option": CONTRACT, "market_context": {"current_stock_price": {"price": 400.0}}}


class DatabaseFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "llm_backtest.db"
        initialize_database(db_path=self.db_path, schema_path=DATA_DIR / "market_schema.sql")
        initialize_news_database(db_path=self.db_path, schema_path=DATA_DIR / "news_schema.sql")
        with closing(get_connection(self.db_path)) as conn, conn:
            self.decision_id = conn.execute(
                "INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision) VALUES ('MSFT', ?, 'neither')",
                (DECISION_AT,),
            ).lastrowid
            conn.execute(
                "INSERT INTO manager_decision_payloads (manager_decision_history_id, manager_input_json, manager_output_json) "
                "VALUES (?, ?, ?)",
                (self.decision_id, json.dumps({"replay_input": {"company": {"symbol": "MSFT"}}}),
                 json.dumps({"recommendation": {"decision": "neither"}})),
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def add_candidate(self, candidate_id: str, decision_id: int, **snapshots: dict) -> None:
        """Link the decision to a candidate that checkpointed `snapshots` (stage -> payload)."""
        manifest = {"pipeline_version": "fixture"}
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("INSERT INTO candidates (candidate_id, symbol, created_at, catalyst_ids_json, article_ids_json, "
                         "pipeline_version_json) VALUES (?, 'MSFT', ?, '[]', '[]', ?)",
                         (candidate_id, DECISION_AT, json.dumps(manifest)))
            conn.execute("UPDATE manager_decision_history SET candidate_id = ? WHERE id = ?", (candidate_id, decision_id))
        for stage, payload in snapshots.items():
            record_candidate_snapshot({"candidate_id": candidate_id, "pipeline_version": manifest}, stage, payload,
                                      db_path=self.db_path)

    def add_closes(self, *closes: tuple[str, float]) -> None:
        for day, close in closes:
            record_historical_underlying_bar({"symbol": "MSFT", "bar_at": f"{day}T05:00:00+00:00", "close": close},
                                             provider="fixture", db_path=self.db_path)

    def backtest(self, contract=CONTRACT, **decision_fields) -> dict:
        return self.backtest_run(contract, **decision_fields)["events"][0]

    def backtest_run(self, contract=CONTRACT, **decision_fields) -> dict:
        decision = {"id": self.decision_id, "symbol": "MSFT", "decision_run_at": DECISION_AT, "manager_decision": "call",
                    "selected_option_symbol": contract["symbol"], "selected_contract": contract, "reference_spot": 400.0,
                    **decision_fields}
        return run_decision_backtest([decision], db_path=self.db_path)


class ModelPricingTests(DatabaseFixture):
    def test_rally_prices_a_call_profit_from_daily_closes(self) -> None:
        self.add_closes(("2026-03-03", 440.0))
        self.assertGreater(self.backtest()["realized_pnl_pct"], 0)

    def test_stored_quotes_take_precedence(self) -> None:
        for quote_at, bid, ask in (("2026-03-02T20:00:00+00:00", 9.8, 10.2), ("2026-03-03T20:00:00+00:00", 12.0, 12.4)):
            record_historical_option_quote({"option_symbol": CONTRACT["symbol"], "quote_at": quote_at, "bid": bid, "ask": ask},
                                           provider="fixture", db_path=self.db_path)
        self.assertEqual(self.backtest()["pricing_source"], "observed_quote")

    def label_earnings(self, day: str) -> None:
        """A stored Yahoo consensus labelled with an earnings date, as production collects it."""
        from services.discovery.company_evidence_extraction import ingest_source
        from tests.test_yahoo_consensus import consensus_doc
        company_id = add_company(add_industry(add_sector("technology", "Technology", db_path=self.db_path), "software",
                                              "Software", db_path=self.db_path), "MSFT", "Microsoft", db_path=self.db_path)
        ingest_source({"id": company_id, "symbol": "MSFT", "cik": "0000789019"},
                      consensus_doc("2026-02-20T12:00:00+00:00", period=day), "2026-02-20T12:00:00+00:00",
                      db_path=self.db_path)

    def test_constant_volatility_across_earnings_is_flagged_and_counted(self) -> None:
        # 2026-11-10: with no quote after the report the model keeps pre-report volatility, about 8 points too kind.
        self.add_closes(("2026-03-03", 440.0))
        unflagged = self.backtest()
        self.label_earnings("2026-03-03")
        run = self.backtest_run()
        event = run["events"][0]
        self.assertIn("constant_iv_across_earnings", event["warnings"])
        self.assertEqual(run["run"]["metrics"]["constant_iv_across_earnings_count"], 1)
        self.assertEqual((event["exit_price"], event["realized_pnl_pct"]),
                         (unflagged["exit_price"], unflagged["realized_pnl_pct"]))

    def test_volatility_observed_after_earnings_is_not_flagged(self) -> None:
        self.add_closes(("2026-03-03", 440.0))
        self.label_earnings("2026-03-03")
        # Another strike's quote that day gives the model a post-report volatility.
        record_historical_option_quote({"option_symbol": "MSFT260417C00420000", "quote_at": "2026-03-03T20:00:00+00:00",
                                        "bid": 25.0, "ask": 25.6}, provider="fixture", db_path=self.db_path)
        self.assertNotIn("constant_iv_across_earnings", self.backtest()["warnings"])

    def test_no_earnings_inside_the_hold_is_not_flagged(self) -> None:
        self.add_closes(("2026-03-03", 440.0))
        self.label_earnings("2026-04-28")
        run = self.backtest_run()
        self.assertNotIn("constant_iv_across_earnings", run["events"][0]["warnings"])
        self.assertEqual(run["run"]["metrics"]["constant_iv_across_earnings_count"], 0)

    def test_crossed_frozen_quote_does_not_fill(self) -> None:
        crossed = {**CONTRACT, "latest_quote": {"bid_price": 11.0, "ask_price": 10.0}}
        self.assertEqual(self.backtest(crossed)["warnings"], ["no_valid_frozen_entry_quote"])


class MarketDataResponse:
    status_code = 203
    headers = {"X-Api-Ratelimit-Consumed": "1", "X-Api-Ratelimit-Remaining": "99"}

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class ProductionGuardrailTests(DatabaseFixture):
    def test_contract_above_the_premium_cap_is_skipped(self) -> None:
        self.assertEqual(self.backtest(max_premium_dollars=800)["warnings"], ["premium_per_trade_cap"])


class ProductionRulesTests(unittest.TestCase):
    LOCAL = WorkerSettings.from_env()
    MANIFEST = {"runtime_settings": {"execute_medium_confidence_trades": False, "max_premium_per_trade_dollars": 800.0},
                "selector_config": {"ALLOW_RISKY_SIMPLE_FALLBACK": False}}

    def rules(self, manifest=MANIFEST) -> ProductionRules:
        return ProductionRules.for_manifest(manifest, local=self.LOCAL)

    def test_recorded_settings_replace_local_ones(self) -> None:
        local = replace(self.LOCAL, execute_medium_confidence_trades=True)
        rules = ProductionRules.for_manifest(self.MANIFEST, local=local)
        self.assertFalse(rules.settings.execute_medium_confidence_trades)

    def test_the_bucket_otm_targets_stay_in_the_recorded_selector_config(self) -> None:
        # Every recorded manifest carries it; dropping it makes every replay a selector_config_mismatch.
        from services.core.pipeline_version import selector_constants
        from services.options.option_dte_buckets import DTE_BUCKET_TO_TARGET_OTM_PCT
        self.assertEqual(selector_constants()["DTE_BUCKET_TO_TARGET_OTM_PCT"], DTE_BUCKET_TO_TARGET_OTM_PCT)


class ReplayFunnelTests(DatabaseFixture):
    def run_with(self, decide, finalized=TRADEABLE) -> dict:
        with patch.object(llm_backtest, "finalize", return_value=finalized):
            return run_llm_backtest(model="local", decide=decide, db_path=self.db_path)

    def test_a_tradeable_answer_becomes_a_backtest_event(self) -> None:
        result = self.run_with(Mock(return_value={"recommendation": TRADEABLE["recommendation"]}))
        self.assertEqual(result["events"][0]["option_symbol"], CONTRACT["symbol"])

    def test_a_blocked_gate_is_not_traded(self) -> None:
        blocked = {**TRADEABLE, "market_regime_gate": {"allowed": False}}
        result = self.run_with(Mock(return_value={"recommendation": TRADEABLE["recommendation"]}), blocked)
        self.assertEqual(result["run"]["metrics"]["replay"]["outcomes"], {"not_eligible": 1})

    def test_repeated_failures_stop_the_run(self) -> None:
        with closing(get_connection(self.db_path)) as conn, conn:
            for minute in range(1, 5):
                history_id = conn.execute(
                    "INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision) VALUES ('MSFT', ?, 'call')",
                    (f"2026-03-02T15:0{minute}:00+00:00",),
                ).lastrowid
                conn.execute("INSERT INTO manager_decision_payloads (manager_decision_history_id, manager_input_json) "
                             "VALUES (?, ?)", (history_id, json.dumps({"replay_input": {"company": {"minute": minute}}})))
        with self.assertRaises(ModelUnavailable):
            self.run_with(Mock(side_effect=RuntimeError("connection refused")))

    def test_a_tampered_snapshot_is_reported_and_the_run_continues(self) -> None:
        with closing(get_connection(self.db_path)) as conn, conn:
            later_id = conn.execute(
                "INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision) VALUES ('MSFT', ?, 'call')",
                ("2026-03-02T16:00:00+00:00",),
            ).lastrowid
            conn.execute("INSERT INTO manager_decision_payloads (manager_decision_history_id, manager_input_json) "
                         "VALUES (?, ?)", (later_id, json.dumps({"replay_input": {"company": {"symbol": "MSFT"}}})))
        self.add_candidate("candidate-1", self.decision_id, pre_contract_selection={"recommendation": {}})
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("UPDATE candidate_snapshots SET snapshot_hash = 'tampered' WHERE candidate_id = 'candidate-1'")
        result = self.run_with(Mock(return_value={"recommendation": TRADEABLE["recommendation"]}))
        self.assertEqual(result["run"]["metrics"]["replay"]["outcomes"], {"snapshot_hash_mismatch": 1, "traded": 1})


class PreDecisionChainTests(DatabaseFixture):
    """A replayed answer naming a bucket production never fetched is selected from the frozen pre-decision chain."""
    PRE_DECISION_CHAIN = {"available": True, "contracts": [CONTRACT]}
    SAVED_CHAIN = {"available": True, "contracts": [{**CONTRACT, "symbol": "MSFT260515C00400000"}]}
    ANSWER = {"recommendation": {"decision": "call", "confidence": "high", "target_dte_bucket": "30_45"}}

    def set_pre_decision_chain(self, option_market: dict) -> None:
        manager_input = {"replay_input": {"company": {"symbol": "MSFT"},
                                          "market_context": {"option_market": option_market}}}
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("UPDATE manager_decision_payloads SET manager_input_json = ? WHERE manager_decision_history_id = ?",
                         (json.dumps(manager_input), self.decision_id))

    def replay(self) -> tuple[dict, Mock]:
        finalize = Mock(return_value=TRADEABLE)
        # The fixture manifest records no selector settings; that mismatch check is not under test here.
        with patch.object(llm_backtest, "finalize", finalize), \
                patch.object(llm_backtest.ProductionRules, "selector_differences", return_value=[]):
            result = run_llm_backtest(model="local", decide=Mock(return_value=self.ANSWER), db_path=self.db_path)
        return result["run"]["metrics"]["replay"], finalize

    def test_an_unsaved_bucket_is_selected_from_the_pre_decision_chain_and_counted(self) -> None:
        self.set_pre_decision_chain(self.PRE_DECISION_CHAIN)
        replay, finalize = self.replay()
        self.assertEqual(finalize.call_args.args[2]["option_market"], self.PRE_DECISION_CHAIN)
        self.assertEqual(replay["outcomes"], {"traded": 1})
        self.assertEqual(replay["pre_decision_chain_trade_count"], 1)

    def test_the_saved_bucket_chain_is_still_preferred(self) -> None:
        self.set_pre_decision_chain(self.PRE_DECISION_CHAIN)
        self.add_candidate("candidate-1", self.decision_id, pre_contract_selection={
            "recommendation": {"decision": "call", "target_dte_bucket": "30_45"},
            "market_context": {"option_market": self.SAVED_CHAIN}})
        replay, finalize = self.replay()
        self.assertEqual(replay["outcomes"], {"traded": 1})
        self.assertEqual(finalize.call_args.args[2]["option_market"], self.SAVED_CHAIN)
        self.assertEqual(replay["pre_decision_chain_trade_count"], 0)

    def test_without_contracts_in_either_chain_it_stays_unavailable(self) -> None:
        self.set_pre_decision_chain({"available": False, "contracts": []})
        replay, finalize = self.replay()
        finalize.assert_not_called()
        self.assertEqual(replay["outcomes"], {"bucket_chain_unavailable": 1})


def decision(symbol: str, at: str) -> dict:
    return {"symbol": symbol, "decision_run_at": at}


class StrategistReplayTests(DatabaseFixture):
    def setUp(self) -> None:
        super().setUp()
        sector_id = add_sector("technology", "Technology", db_path=self.db_path)
        company_id = add_company(add_industry(sector_id, "software", "Software", db_path=self.db_path), "MSFT",
                                 "Microsoft", db_path=self.db_path)
        manager_input = {"replay_input": {"company": {"symbol": "MSFT"},
                                          "strategist_recommendation": {"decision": "trade_candidate"}}}
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("UPDATE manager_decision_payloads SET manager_input_json = ? WHERE manager_decision_history_id = ?",
                         (json.dumps(manager_input), self.decision_id))
        self.add_candidate("candidate-1", self.decision_id,
                           pre_strategist={"company": {"symbol": "MSFT", "company_id": company_id}})

    def run_replay(self, answer: dict | None = None, **options) -> tuple[dict, Mock, Mock]:
        manager = Mock(return_value={"recommendation": {"decision": "neither"}})
        strategist = Mock(return_value={"recommendation": answer or {"decision": "do_not_trade", "confidence": "low"}})
        with patch.object(llm_backtest, "finalize", return_value=TRADEABLE):
            result = run_llm_backtest(model="local", decide=manager, decide_strategist=strategist,
                                      replay_strategist=True, db_path=self.db_path, **options)
        return result, manager, strategist

    def test_the_manager_answers_the_replayed_strategist(self) -> None:
        _, manager, _ = self.run_replay()
        self.assertEqual(manager.call_args.args[0]["strategist_recommendation"]["decision"], "do_not_trade")

    def test_another_sample_asks_the_strategist_again_and_is_cached_itself(self) -> None:
        # Measures how often the same strategist input gets a different thesis, not just a different manager answer.
        calls = [self.run_replay(sample=sample)[2].call_count for sample in (0, 0, 1, 1)]
        self.assertEqual(calls, [1, 0, 1, 0])

    def test_thesis_status_is_counted_per_strategist_decision_for_production_and_replay(self) -> None:
        production = {"decision": "trade_candidate", "evidence_thesis_check": {"status": "deferred"}}
        manager_input = {"replay_input": {"company": {"symbol": "MSFT"}, "strategist_recommendation": production}}
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("UPDATE manager_decision_payloads SET manager_input_json = ? WHERE manager_decision_history_id = ?",
                         (json.dumps(manager_input), self.decision_id))
        result, _, _ = self.run_replay({"decision": "watchlist", "evidence_thesis_check": {"status": "validated"}})
        self.assertEqual(result["run"]["metrics"]["replay"]["strategist_replay"]["thesis_status_by_decision"], {
            "production": {"trade_candidate": {"deferred": 1}}, "replay": {"watchlist": {"validated": 1}}})

    def test_replayed_answers_are_reused_under_the_production_rule(self) -> None:
        def strategist_input(price: float, reason: str = "Guidance raised") -> dict:
            return {"company": {"symbol": "MSFT", "market_data": {"last_price": price}},
                    "views": {"company_view": {"agent_conclusions": [{"article_id": 7, "reason": reason}]}},
                    "grounding": {"article_ids": [7]}, "market_context": {"market_regime": {"state": "range"}}}

        def replay(reuse_minutes: int) -> tuple[list[bool], Mock]:
            decide = Mock(return_value={"recommendation": {"decision": "watchlist"}})
            strategist = llm_backtest.StrategistReplay(
                model=f"local-{reuse_minutes}", provider="fixture", thesis_policy=EvidenceThesisPolicy.from_env(),
                decide=decide, db_path=self.db_path, reuse_max_age_minutes=reuse_minutes)
            start = datetime.fromisoformat(DECISION_AT)
            asked = [(timedelta(0), strategist_input(400.0)), (timedelta(minutes=30), strategist_input(401.0)),
                     (timedelta(minutes=60), strategist_input(401.0, "Guidance cut")),
                     (timedelta(minutes=200), strategist_input(401.5, "Guidance cut"))]
            reused = [strategist.answer(payload, symbol="MSFT", decided_at=start + offset)[2] for offset, payload in asked]
            return reused, decide

        reused, decide = replay(120)
        self.assertEqual(reused, [False, True, False, False])
        self.assertEqual(decide.call_count, 3)
        reused, decide = replay(0)
        self.assertEqual(reused, [False] * 4)
        self.assertEqual(decide.call_count, 4)

    def test_reuse_needs_strategist_replay(self) -> None:
        with self.assertRaises(ValueError):
            run_llm_backtest(model="local", strategist_reuse_max_age_minutes=120, db_path=self.db_path)


class ThesisRecheckTests(DatabaseFixture):
    """`recheck_thesis` re-runs this checkout's thesis checker on the stored thesis; only the manager is asked."""
    OLD_CHECK = {"evaluation_id": "e1", "status": "deferred", "package_id": "pkg", "research_status": "legacy",
                 "failures": ["central_claim_failed"], "failed_claims": [{"claim_id": "c1", "central": True,
                                                                          "failures": ["unsupported_number:2026"]}]}
    NEW_CHECK = {"schema_version": "s", "policy_version": "p", "status": "passed", "package_id": "pkg",
                 "research_status": "legacy", "failures": [], "claims": [], "thesis": {"direction": "up"},
                 "semantic_review": {"status": "not_run"}}

    def setUp(self) -> None:
        super().setUp()
        strategist = {"decision": "trade_candidate", "evidence_thesis": {"direction": "up"},
                      "evidence_thesis_check": self.OLD_CHECK, "evidence_thesis_handoff": "same_package"}
        manager_input = {"replay_input": {"company": {"symbol": "MSFT"}, "company_evidence": {"package_id": "pkg"},
                                          "strategist_recommendation": strategist}}
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("UPDATE manager_decision_payloads SET manager_input_json = ? WHERE manager_decision_history_id = ?",
                         (json.dumps(manager_input), self.decision_id))

    def run_replay(self, **options) -> tuple[dict, Mock, Mock]:
        manager = Mock(return_value={"recommendation": {"decision": "neither"}})
        with patch.object(llm_backtest, "finalize", return_value=TRADEABLE), \
                patch.object(llm_backtest, "check_evidence_thesis", return_value=self.NEW_CHECK) as check:
            result = run_llm_backtest(model="local", decide=manager, db_path=self.db_path, **options)
        return result, manager, check

    def test_the_manager_sees_the_rechecked_thesis(self) -> None:
        result, manager, check = self.run_replay(recheck_thesis=True)
        seen = manager.call_args.args[0]["strategist_recommendation"]["evidence_thesis_check"]
        self.assertEqual((seen["status"], seen["failures"], seen["failed_claims"]), ("passed", [], []))
        self.assertEqual(seen["evaluation_id"], "e1")
        self.assertEqual(check.call_args.args[0], {"direction": "up"})
        self.assertEqual(result["run"]["metrics"]["replay"]["thesis_recheck"], {"rechecked": 1, "changed": 1})

    def test_without_the_flag_the_stored_check_is_kept(self) -> None:
        result, manager, check = self.run_replay()
        check.assert_not_called()
        self.assertEqual(manager.call_args.args[0]["strategist_recommendation"]["evidence_thesis_check"], self.OLD_CHECK)
        self.assertIsNone(result["run"]["metrics"]["replay"]["thesis_recheck"])

    def test_recheck_is_refused_with_a_replayed_strategist(self) -> None:
        with self.assertRaises(ValueError):
            run_llm_backtest(model="local", recheck_thesis=True, replay_strategist=True, db_path=self.db_path)


class SampleTests(DatabaseFixture):
    """`sample` asks the model again for an input it already answered; sample 0 is the existing cache entry."""

    def run_sample(self, manager: Mock, **options) -> dict:
        with patch.object(llm_backtest, "finalize", return_value=TRADEABLE):
            return run_llm_backtest(model="local", decide=manager, db_path=self.db_path, **options)

    def test_sample_zero_reads_the_existing_cache(self) -> None:
        manager = Mock(return_value={"recommendation": {"decision": "neither"}})
        self.run_sample(manager)
        result = self.run_sample(manager, sample=0)
        self.assertEqual(manager.call_count, 1)
        self.assertEqual(result["run"]["metrics"]["replay"]["sample"], 0)

    def test_another_sample_asks_the_model_again_and_is_cached_itself(self) -> None:
        manager = Mock(return_value={"recommendation": {"decision": "neither"}})
        self.run_sample(manager)
        result = self.run_sample(manager, sample=1)
        self.assertEqual(manager.call_count, 2)
        self.assertEqual(result["run"]["metrics"]["replay"]["sample"], 1)
        self.run_sample(manager, sample=1)
        self.assertEqual(manager.call_count, 2)

    def test_samples_need_a_model_to_ask(self) -> None:
        for bad in ({"model": "stored", "sample": 1}, {"model": "local", "sample": -1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                run_llm_backtest(db_path=self.db_path, **bad)


class RecommendationEligibilityTests(unittest.TestCase):
    def test_recorded_decisions_skip_the_admission_check(self) -> None:
        view = {"decision": "call", "confidence": "high", "strategist_recommendation": {"decision": "trade_candidate"},
                "entry_gate": {"allowed": True}, "market_regime_gate": {"allowed": True}}
        with patch("services.trading.order_candidates.catalyst_admission_allowed", return_value=False):
            self.assertTrue(OrderCandidateBuilder().recommendation_allows_entry(view))


class ItemFilterTests(DatabaseFixture):
    """`decision_ids` restricts a replay to explicit decisions. The fixture's own decision is 2026-03-02 15:00."""

    def setUp(self) -> None:
        super().setUp()
        # Inserted out of time order, so row order and chronological order differ.
        self.a = self.add_decision("2026-03-05T15:00:00+00:00")
        self.b = self.add_decision("2026-03-03T15:00:00+00:00")
        self.c = self.add_decision("2026-03-04T15:00:00+00:00")
        self.d = self.add_decision("2026-03-06T15:00:00+00:00")

    def add_decision(self, at: str) -> int:
        with closing(get_connection(self.db_path)) as conn, conn:
            history_id = conn.execute("INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision) "
                                      "VALUES ('MSFT', ?, 'neither')", (at,)).lastrowid
            conn.execute("INSERT INTO manager_decision_payloads (manager_decision_history_id, manager_input_json, "
                         "manager_output_json) VALUES (?, ?, ?)",
                         (history_id, json.dumps({"replay_input": {"company": {"symbol": "MSFT", "at": at}}}),
                          json.dumps({"recommendation": {"decision": "neither"}})))
        return history_id

    def listed(self, **options) -> list[int]:
        from market_db_modules.llm_replay_store import list_frozen_manager_decisions
        return [row["id"] for row in list_frozen_manager_decisions(db_path=self.db_path, **options)]

    def test_only_the_listed_decisions_come_back_in_chronological_order(self) -> None:
        self.assertEqual(self.listed(ids=[self.a, self.c, self.b]), [self.b, self.c, self.a])

    def test_no_ids_means_every_decision(self) -> None:
        self.assertEqual(self.listed(ids=None), [self.decision_id, self.b, self.c, self.a, self.d])

    def test_an_empty_list_matches_nothing(self) -> None:
        self.assertEqual(self.listed(ids=[]), [])

    def test_unknown_and_repeated_ids_are_ignored(self) -> None:
        self.assertEqual(self.listed(ids=[self.b, self.b, 999_999]), [self.b])

    def test_limit_applies_after_the_id_filter(self) -> None:
        # The two most recent of the listed decisions, though c and d (unlisted) are newer than b.
        self.assertEqual(self.listed(ids=[self.decision_id, self.b, self.a], limit=2), [self.b, self.a])

    def test_the_window_still_applies_with_ids(self) -> None:
        self.assertEqual(self.listed(ids=[self.b, self.a], start_at="2026-03-04T00:00:00+00:00"), [self.a])

    def test_a_very_long_id_list_is_chunked_and_still_limited_across_chunks(self) -> None:
        # More than any SQLite variable limit; the listed decisions are spread over different chunks.
        ids = list(range(1_000_000, 1_040_000)) + [self.decision_id, self.b, self.a]
        self.assertEqual(self.listed(ids=ids), [self.decision_id, self.b, self.a])
        self.assertEqual(self.listed(ids=[self.decision_id, *range(1_000_000, 1_040_000), self.b, self.a], limit=2),
                         [self.b, self.a])

    def test_the_replay_runs_only_the_listed_decisions_in_order(self) -> None:
        decide = Mock(return_value={"recommendation": {"decision": "neither"}})
        run = llm_backtest.ReplayRun.build(model="local", decide=decide, decision_ids=[self.a, self.c, self.b],
                                           db_path=self.db_path)
        self.assertEqual([d["id"] for d in run.decisions], [self.b, self.c, self.a])
        with patch.object(llm_backtest, "finalize", return_value=TRADEABLE):
            run_llm_backtest(model="local", decide=decide, decision_ids=[self.a, self.c, self.b], db_path=self.db_path)
        self.assertEqual([call.args[0]["company"]["at"] for call in decide.call_args_list],
                         ["2026-03-03T15:00:00+00:00", "2026-03-04T15:00:00+00:00", "2026-03-05T15:00:00+00:00"])

    def test_an_empty_id_list_replays_nothing_and_does_not_raise(self) -> None:
        decide = Mock()
        with patch.object(llm_backtest, "finalize", return_value=TRADEABLE):
            result = run_llm_backtest(model="local", decide=decide, decision_ids=[], db_path=self.db_path)
        decide.assert_not_called()
        self.assertEqual(result["events"], [])

    def test_limit_still_trims_a_replay_that_has_decision_ids(self) -> None:
        run = llm_backtest.ReplayRun.build(model="stored", decision_ids=[self.b, self.c, self.a], limit=2,
                                           db_path=self.db_path)
        self.assertEqual([d["id"] for d in run.decisions], [self.c, self.a])


class ContaminationMetricsTests(DatabaseFixture):
    """A known-cutoff model's run records what the guard excluded. Fixture CSV: test-model, cutoff 2026-01-01."""

    def test_the_exclusion_and_cutoff_appear_in_the_replay_metrics(self) -> None:
        with closing(get_connection(self.db_path)) as conn, conn:
            history_id = conn.execute("INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision) "
                                      "VALUES ('MSFT', '2026-01-15T15:00:00+00:00', 'neither')").lastrowid
            conn.execute("INSERT INTO manager_decision_payloads (manager_decision_history_id, manager_input_json) "
                         "VALUES (?, ?)", (history_id, json.dumps({"replay_input": {"company": {"symbol": "OLD"}}})))
        decide = Mock(return_value={"recommendation": TRADEABLE["recommendation"]})
        fixture_csv = Path(__file__).resolve().parent / "fixtures" / "model_cutoffs.csv"
        with patch("services.evaluation.model_cutoffs.CUTOFFS_CSV", fixture_csv), \
                patch.object(llm_backtest, "finalize", return_value=TRADEABLE):
            result = run_llm_backtest(model="test-model", decide=decide, db_path=self.db_path)
        replay = result["run"]["metrics"]["replay"]
        self.assertEqual(replay["excluded_pre_knowledge_cutoff"], 1)
        self.assertEqual(replay["contamination"], {"knowledge_cutoff": "2026-01-01", "margin_days": 30,
                                                   "source": "https://example.test/models/test-model"})
        self.assertEqual(decide.call_count, 1)

    def test_an_allowed_unknown_cutoff_replays_everything_and_records_no_cutoff(self) -> None:
        decide = Mock(return_value={"recommendation": TRADEABLE["recommendation"]})
        fixture_csv = Path(__file__).resolve().parent / "fixtures" / "model_cutoffs.csv"
        with patch("services.evaluation.model_cutoffs.CUTOFFS_CSV", fixture_csv), \
                patch.object(llm_backtest, "finalize", return_value=TRADEABLE):
            result = run_llm_backtest(model="mystery-model", decide=decide, allow_unknown_cutoff=True,
                                      db_path=self.db_path)
        replay = result["run"]["metrics"]["replay"]
        self.assertEqual(replay["excluded_pre_knowledge_cutoff"], 0)
        self.assertEqual(replay["contamination"], {"knowledge_cutoff": None, "margin_days": 30, "source": None})
        self.assertEqual(decide.call_count, 1)

    def test_an_unknown_model_is_refused_before_anything_is_replayed(self) -> None:
        decide = Mock()
        with patch("services.evaluation.model_cutoffs.CUTOFFS_CSV", Path(__file__).resolve().parent / "fixtures" / "model_cutoffs.csv"):
            with self.assertRaises(ValueError):
                run_llm_backtest(model="mystery-model", decide=decide, db_path=self.db_path)
        decide.assert_not_called()


class CommandLineFlagTests(unittest.TestCase):
    """`agentCallers/llm_backtest.py` passes the item list and the unknown-cutoff override through to the runners."""

    def run_cli(self, *argv: str, direction: bool = False) -> dict:
        import importlib
        import io
        import sys
        from contextlib import redirect_stdout
        cli = importlib.import_module("agentCallers.llm_backtest")
        runner = "run_direction_score" if direction else "run_llm_backtest"
        result = {"model": "m", "replay": {}, "score": {}, "decisions": []} if direction else \
            {"run": {"id": 1, "model": "m", "prompt_version": "p", "metrics": {}}}
        with patch.object(sys, "argv", ["llm_backtest", "--model", "local", *argv]), \
                patch.object(cli, runner, return_value=result) as run, redirect_stdout(io.StringIO()):
            cli.main()
        return run.call_args.kwargs

    def ids_file(self, text: str) -> str:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "ids.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_by_default_there_is_no_item_filter_and_the_guard_is_strict(self) -> None:
        kwargs = self.run_cli()
        self.assertIsNone(kwargs["decision_ids"])
        self.assertFalse(kwargs["allow_unknown_cutoff"])

    def test_the_ids_file_lists_one_decision_id_per_line(self) -> None:
        self.assertEqual(self.run_cli("--decision-ids-file", self.ids_file("7\n3\n\n12\n"))["decision_ids"], [7, 3, 12])

    def test_the_override_flag_reaches_the_runner(self) -> None:
        self.assertTrue(self.run_cli("--allow-unknown-cutoff")["allow_unknown_cutoff"])

    def test_the_recheck_flag_reaches_the_runner(self) -> None:
        self.assertFalse(self.run_cli()["recheck_thesis"])
        self.assertTrue(self.run_cli("--recheck-thesis")["recheck_thesis"])

    def test_the_sample_flag_reaches_the_runner(self) -> None:
        self.assertEqual(self.run_cli()["sample"], 0)
        self.assertEqual(self.run_cli("--sample", "3")["sample"], 3)

    def test_direction_scoring_gets_the_same_options(self) -> None:
        kwargs = self.run_cli("--score-direction", "--decision-ids-file", self.ids_file("5\n6\n"),
                              "--allow-unknown-cutoff", direction=True)
        self.assertEqual(kwargs["decision_ids"], [5, 6])
        self.assertTrue(kwargs["allow_unknown_cutoff"])


if __name__ == "__main__":
    unittest.main()
