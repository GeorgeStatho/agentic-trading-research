"""LLM replay backtests: model pricing, answer caching and the replay funnel, with no model server."""
from __future__ import annotations

from contextlib import closing
import json
from dataclasses import replace
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
from datetime import datetime, timezone
from market_db_modules.backtest_marketdata import fetch_marketdata_eod_quotes_for_contracts
from market_db_modules.backtesting import MODELLED_EOD_IV_PRICING_SOURCE, MODELLED_PRICING_SOURCE, run_decision_backtest
from services import llm_backtest
from services.config import FrontMainSettings
from services.llm_backtest import (CachedManagerReplay, ModelUnavailable, ProductionRules, fetch_quotes_for_trades,
                                   fill_missing_daily_closes, run_llm_backtest, stored_recommendation)
from services.order_candidates import OrderCandidateBuilder

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

    def test_modelled_trade_is_labelled(self) -> None:
        self.add_closes(("2026-03-03", 440.0))
        self.assertEqual(self.backtest()["pricing_source"], MODELLED_PRICING_SOURCE)

    def test_stored_quotes_take_precedence(self) -> None:
        for quote_at, bid, ask in (("2026-03-02T20:00:00+00:00", 9.8, 10.2), ("2026-03-03T20:00:00+00:00", 12.0, 12.4)):
            record_historical_option_quote({"option_symbol": CONTRACT["symbol"], "quote_at": quote_at, "bid": bid, "ask": ask},
                                           provider="fixture", db_path=self.db_path)
        self.assertEqual(self.backtest()["pricing_source"], "observed_quote")

    def test_missing_underlying_closes_leave_the_position_unexited(self) -> None:
        self.assertEqual(self.backtest()["warnings"], ["no_underlying_close_after_entry"])

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


class ObservedVolatilityTests(DatabaseFixture):
    NEIGHBOUR = "MSFT260417C00440000"

    def quote_neighbour(self, bid: float, ask: float) -> None:
        record_historical_option_quote({"option_symbol": self.NEIGHBOUR, "quote_at": "2026-03-03T21:00:00+00:00",
                                        "bid": bid, "ask": ask}, provider="fixture", db_path=self.db_path)

    def test_same_day_quote_supplies_the_volatility(self) -> None:
        self.add_closes(("2026-03-03", 440.0))
        self.quote_neighbour(14.0, 14.4)
        self.assertEqual(self.backtest()["pricing_source"], MODELLED_EOD_IV_PRICING_SOURCE)

    def test_volatility_collapse_lowers_the_exit(self) -> None:
        self.add_closes(("2026-03-03", 440.0))
        constant_iv_pnl = self.backtest()["realized_pnl_pct"]
        self.quote_neighbour(6.0, 6.3)
        self.assertLess(self.backtest()["realized_pnl_pct"], constant_iv_pnl)

    def test_wide_same_day_quotes_are_ignored(self) -> None:
        self.add_closes(("2026-03-03", 440.0))
        self.quote_neighbour(5.0, 14.0)
        self.assertEqual(self.backtest()["pricing_source"], MODELLED_PRICING_SOURCE)


class ContractQuoteFetchTests(DatabaseFixture):
    WINDOW = {"decision": {"id": None, "decision_run_at": DECISION_AT}, "option_symbol": CONTRACT["symbol"],
              "start_at": DECISION_AT, "end_at": "2026-03-10T15:00:00+00:00"}

    def payload(self) -> dict:
        days = ["2026-03-02T16:00:00-05:00", "2026-03-10T16:00:00-04:00"]
        return {"s": "ok", "optionSymbol": [CONTRACT["symbol"]] * 2, "bid": [9.8, 12.0], "ask": [10.2, 12.4],
                "updated": [int(datetime.fromisoformat(day).timestamp()) for day in days]}

    def test_replay_contract_quotes_are_stored(self) -> None:
        window = {**self.WINDOW, "decision": {"id": self.decision_id, "decision_run_at": DECISION_AT}}
        result = fetch_marketdata_eod_quotes_for_contracts([window], token="fixture", db_path=self.db_path,
                                                           request_get=lambda *args, **kwargs: MarketDataResponse(self.payload()))
        self.assertEqual(result["inserted_quote_count"], 2)

    def test_missing_token_is_reported_not_raised(self) -> None:
        trade = {"id": self.decision_id, "decision_run_at": DECISION_AT, "selected_option_symbol": CONTRACT["symbol"]}
        with patch.dict("os.environ", {"MARKETDATA_TOKEN": ""}):
            summary = fetch_quotes_for_trades([trade], max_hold_hours=168, db_path=self.db_path)
        self.assertEqual(summary["gap_reasons"], {"missing_marketdata_token": 1})


class ProductionGuardrailTests(DatabaseFixture):
    def test_contract_above_the_premium_cap_is_skipped(self) -> None:
        self.assertEqual(self.backtest(max_premium_dollars=800)["warnings"], ["premium_per_trade_cap"])

    def test_contract_within_the_premium_cap_is_priced(self) -> None:
        self.add_closes(("2026-03-03", 440.0))
        self.assertEqual(self.backtest(max_premium_dollars=1500)["status"], "simulated_closed")

    def test_every_candidate_is_scored_before_portfolio_limits(self) -> None:
        self.add_closes(("2026-03-03", 440.0))
        metrics = self.backtest_run()["run"]["metrics"]
        self.assertEqual(metrics["before_portfolio_limits"]["priced_entry_count"], 1)

    def test_missing_daily_closes_are_fetched_for_traded_symbols(self) -> None:
        bars = [{"bar_at": "2026-03-03T05:00:00+00:00", "close": 440.0}]
        fill_missing_daily_closes({"MSFT"}, since=DECISION_AT, db_path=self.db_path, fetch=lambda symbol: ("fixture", bars))
        self.assertEqual(self.backtest()["status"], "simulated_closed")


class ProductionRulesTests(unittest.TestCase):
    LOCAL = FrontMainSettings.from_env()
    MANIFEST = {"runtime_settings": {"execute_medium_confidence_trades": False, "max_premium_per_trade_dollars": 800.0},
                "selector_config": {"ALLOW_RISKY_SIMPLE_FALLBACK": False}}

    def rules(self, manifest=MANIFEST) -> ProductionRules:
        with patch.object(llm_backtest, "get_candidate_manifest", return_value=manifest):
            return ProductionRules.for_candidate("candidate", local=self.LOCAL)

    def test_recorded_settings_replace_local_ones(self) -> None:
        local = replace(self.LOCAL, execute_medium_confidence_trades=True)
        with patch.object(llm_backtest, "get_candidate_manifest", return_value=self.MANIFEST):
            rules = ProductionRules.for_candidate("candidate", local=local)
        self.assertFalse(rules.settings.execute_medium_confidence_trades)

    def test_medium_confidence_override_allows_medium_entries(self) -> None:
        self.assertTrue(self.rules().executing_medium_confidence().settings.execute_medium_confidence_trades)

    def test_override_keeps_the_other_recorded_settings(self) -> None:
        self.assertEqual(self.rules().executing_medium_confidence().settings.max_premium_per_trade_dollars, 800.0)

    def test_differing_selector_settings_are_named(self) -> None:
        current = {"ALLOW_RISKY_SIMPLE_FALLBACK": True}
        self.assertEqual(self.rules().selector_differences(current), ["ALLOW_RISKY_SIMPLE_FALLBACK"])

    def test_without_a_manifest_local_settings_apply(self) -> None:
        self.assertEqual(self.rules(manifest=None).selector_differences({"ANY": 1}), [])


class CachedReplayTests(DatabaseFixture):
    def test_second_request_reads_the_cache(self) -> None:
        decide = Mock(return_value={"recommendation": {"decision": "call"}})
        replay = CachedManagerReplay(model="local", provider="llamacpp", decide=decide, db_path=self.db_path)
        replay.recommendation({"company": {"symbol": "MSFT"}})
        replay.recommendation({"company": {"symbol": "MSFT"}})
        self.assertEqual(decide.call_count, 1)

    def test_a_different_model_label_asks_again(self) -> None:
        decide = Mock(return_value={"recommendation": {"decision": "call"}})
        for model in ("bonsai-a", "bonsai-b"):
            CachedManagerReplay(model=model, provider="llamacpp", decide=decide, db_path=self.db_path).recommendation({})
        self.assertEqual(decide.call_count, 2)


class ReplayFunnelTests(DatabaseFixture):
    def run_with(self, decide, finalized=TRADEABLE) -> dict:
        with patch.object(llm_backtest, "finalize", return_value=finalized):
            return run_llm_backtest(model="local", decide=decide, db_path=self.db_path)

    def test_a_tradeable_answer_becomes_a_backtest_event(self) -> None:
        result = self.run_with(Mock(return_value={"recommendation": TRADEABLE["recommendation"]}))
        self.assertEqual(result["events"][0]["option_symbol"], CONTRACT["symbol"])

    def test_direction_changes_are_counted(self) -> None:
        result = self.run_with(Mock(return_value={"recommendation": TRADEABLE["recommendation"]}))
        self.assertEqual(result["run"]["metrics"]["replay"]["changed_direction_count"], 1)

    def test_medium_answers_trade_only_with_the_override(self) -> None:
        medium = {**TRADEABLE, "recommendation": {**TRADEABLE["recommendation"], "confidence": "medium"}}
        local = replace(FrontMainSettings.from_env(), execute_medium_confidence_trades=False)
        decide = Mock(return_value={"recommendation": medium["recommendation"]})
        outcomes = []
        for override in (False, True):
            with patch.object(llm_backtest.FrontMainSettings, "from_env", return_value=local), \
                    patch.object(llm_backtest, "finalize", return_value=medium):
                result = run_llm_backtest(model=f"local-{override}", decide=decide, db_path=self.db_path,
                                          execute_medium_confidence=override)
            outcomes.append(result["run"]["metrics"]["replay"]["outcomes"])
        self.assertEqual(outcomes, [{"not_eligible": 1}, {"traded": 1}])

    def test_a_blocked_gate_is_not_traded(self) -> None:
        blocked = {**TRADEABLE, "market_regime_gate": {"allowed": False}}
        result = self.run_with(Mock(return_value={"recommendation": TRADEABLE["recommendation"]}), blocked)
        self.assertEqual(result["run"]["metrics"]["replay"]["outcomes"], {"not_eligible": 1})

    def test_a_single_model_failure_is_recorded(self) -> None:
        result = self.run_with(Mock(side_effect=RuntimeError("bad json")))
        self.assertEqual(result["run"]["metrics"]["replay"]["outcomes"], {"model_error": 1})

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


class SelectionChainTests(unittest.TestCase):
    PAYLOAD = {"market_context": {"option_market": {"contracts": ["pre-decision"]}}}
    SAVED = {"target_dte_bucket": "30_45", "option_market": {"contracts": ["bucket"]}}

    def market(self, recommendation, saved=SAVED):
        with patch.object(llm_backtest, "get_saved_bucket_chain", return_value=saved):
            return llm_backtest.selection_market_context(self.PAYLOAD, recommendation, "candidate")

    def test_matching_bucket_selects_from_the_saved_chain(self) -> None:
        market = self.market({"decision": "call", "target_dte_bucket": "30_45"})
        self.assertEqual(market["option_market"]["contracts"], ["bucket"])

    def test_another_bucket_has_no_chain(self) -> None:
        self.assertIsNone(self.market({"decision": "call", "target_dte_bucket": "12_20"}))

    def test_missing_snapshot_has_no_chain(self) -> None:
        self.assertIsNone(self.market({"decision": "put", "target_dte_bucket": "30_45"}, saved=None))

    def test_bucket_none_keeps_the_pre_decision_chain(self) -> None:
        market = self.market({"decision": "call", "target_dte_bucket": "none"})
        self.assertEqual(market["option_market"]["contracts"], ["pre-decision"])


def decision(symbol: str, at: str) -> dict:
    return {"symbol": symbol, "decision_run_at": at}


class DecisionSpacingTests(unittest.TestCase):
    def kept(self, decisions, hours=4) -> list[str]:
        spaced = llm_backtest.space_decisions_per_symbol(decisions, min_hours=hours)
        return [f"{item['symbol']} {item['decision_run_at'][11:16]}" for item in spaced]

    def test_decisions_inside_the_gap_are_skipped(self) -> None:
        decisions = [decision("NVDA", f"2026-09-21T{hour}:00:00+00:00") for hour in (14, 15, 17, 18)]
        self.assertEqual(self.kept(decisions), ["NVDA 14:00", "NVDA 18:00"])

    def test_the_gap_is_measured_from_the_last_kept_decision(self) -> None:
        decisions = [decision("NVDA", f"2026-09-21T{hour}:00:00+00:00") for hour in (10, 13, 15)]
        self.assertEqual(self.kept(decisions, hours=4), ["NVDA 10:00", "NVDA 15:00"])

    def test_each_symbol_has_its_own_gap(self) -> None:
        decisions = [decision("NVDA", "2026-09-21T14:00:00+00:00"), decision("CRWD", "2026-09-21T14:30:00+00:00")]
        self.assertEqual(self.kept(decisions), ["NVDA 14:00", "CRWD 14:30"])


class SpacedReplayTests(DatabaseFixture):
    def test_spaced_out_decisions_are_not_replayed_and_are_counted(self) -> None:
        with closing(get_connection(self.db_path)) as conn, conn:
            history_id = conn.execute(
                "INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision) "
                "VALUES ('MSFT', '2026-03-02T16:00:00+00:00', 'neither')").lastrowid
            conn.execute("INSERT INTO manager_decision_payloads (manager_decision_history_id, manager_input_json) "
                         "VALUES (?, ?)", (history_id, json.dumps({"replay_input": {"company": {"symbol": "MSFT"}}})))
        decide = Mock(return_value={"recommendation": {"decision": "neither"}})
        with patch.object(llm_backtest, "finalize", return_value=TRADEABLE):
            result = run_llm_backtest(model="local", decide=decide, db_path=self.db_path, min_hours_between=4)
        replay = result["run"]["metrics"]["replay"]
        self.assertEqual((replay["source_decision_count"], replay["spaced_out_decision_count"]), (1, 1))


class StrategistReplayTests(DatabaseFixture):
    def setUp(self) -> None:
        super().setUp()
        sector_id = add_sector("technology", "Technology", db_path=self.db_path)
        company_id = add_company(add_industry(sector_id, "software", "Software", db_path=self.db_path), "MSFT",
                                 "Microsoft", db_path=self.db_path)
        manager_input = {"replay_input": {"company": {"symbol": "MSFT"},
                                          "strategist_recommendation": {"decision": "trade_candidate"}}}
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("UPDATE manager_decision_history SET candidate_id = 'candidate-1' WHERE id = ?", (self.decision_id,))
            conn.execute("UPDATE manager_decision_payloads SET manager_input_json = ? WHERE manager_decision_history_id = ?",
                         (json.dumps(manager_input), self.decision_id))
            conn.execute(
                "INSERT INTO strategist_runs (candidate_id, company_id, model, prompt_version, input_article_ids_json, "
                "input_payload_json, status) VALUES ('candidate-1', ?, 'production', 'v', '[]', ?, 'completed')",
                (company_id, json.dumps({"company": {"symbol": "MSFT", "company_id": company_id}})))

    def run_replay(self, **options) -> tuple[dict, Mock, Mock]:
        manager = Mock(return_value={"recommendation": {"decision": "neither"}})
        strategist = Mock(return_value={"recommendation": {"decision": "do_not_trade", "confidence": "low"}})
        with patch.object(llm_backtest, "finalize", return_value=TRADEABLE):
            result = run_llm_backtest(model="local", decide=manager, decide_strategist=strategist,
                                      replay_strategist=True, db_path=self.db_path, **options)
        return result, manager, strategist

    def test_the_manager_answers_the_replayed_strategist(self) -> None:
        _, manager, _ = self.run_replay()
        self.assertEqual(manager.call_args.args[0]["strategist_recommendation"]["decision"], "do_not_trade")

    def test_the_strategist_answers_its_frozen_input(self) -> None:
        _, _, strategist = self.run_replay()
        self.assertEqual(strategist.call_args.args[0]["company"]["symbol"], "MSFT")

    def test_a_changed_strategist_decision_is_counted(self) -> None:
        result, _, _ = self.run_replay()
        self.assertEqual(result["run"]["metrics"]["replay"]["strategist_replay"]["changed_decision_count"], 1)

    def test_strategist_answers_are_cached(self) -> None:
        self.run_replay()
        _, _, strategist = self.run_replay()
        strategist.assert_not_called()

    def test_a_decision_without_a_strategist_run_is_reported(self) -> None:
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute("DELETE FROM strategist_runs")
        result, _, _ = self.run_replay()
        self.assertEqual(result["run"]["metrics"]["replay"]["outcomes"], {"missing_strategist_input": 1})

    def test_stored_answers_cannot_replay_the_strategist(self) -> None:
        with self.assertRaises(ValueError):
            run_llm_backtest(model="stored", replay_strategist=True, db_path=self.db_path)

    def test_without_the_option_production_strategist_is_kept(self) -> None:
        manager = Mock(return_value={"recommendation": {"decision": "neither"}})
        with patch.object(llm_backtest, "finalize", return_value=TRADEABLE):
            result = run_llm_backtest(model="local", decide=manager, db_path=self.db_path)
        self.assertIsNone(result["run"]["metrics"]["replay"]["strategist_replay"])


class ProgressEstimateTests(unittest.TestCase):
    NOW = datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc)

    def test_finish_time_is_paced_by_model_calls(self) -> None:
        progress = llm_backtest.ProgressEstimate(total=10)
        progress.record_model_call(60)
        progress.record_model_call(120)
        self.assertIn("estimated finish 2026-10-11 20:07", progress.report(5, now=self.NOW))

    def test_remaining_decisions_are_counted(self) -> None:
        self.assertIn("5/12 done, 7 remaining", llm_backtest.ProgressEstimate(total=12).report(5, now=self.NOW))


class StoredAnswerTests(unittest.TestCase):
    def test_selector_fields_are_removed_before_refinalizing(self) -> None:
        decision = {"manager_output_json": json.dumps({"recommendation": {
            "decision": "call", "confidence": "high", "selected_option_id": "x", "selection_debug": {}}})}
        self.assertEqual(stored_recommendation(decision), {"decision": "call", "confidence": "high"})


class RecommendationEligibilityTests(unittest.TestCase):
    def test_recorded_decisions_skip_the_admission_check(self) -> None:
        view = {"decision": "call", "confidence": "high", "strategist_recommendation": {"decision": "trade_candidate"},
                "entry_gate": {"allowed": True}, "market_regime_gate": {"allowed": True}}
        with patch("services.order_candidates.catalyst_admission_allowed", return_value=False):
            self.assertTrue(OrderCandidateBuilder().recommendation_allows_entry(view))


if __name__ == "__main__":
    unittest.main()
