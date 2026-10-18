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
from services import llm_backtest
from services.config import FrontMainSettings
from services.evidence_thesis_policy import EvidenceThesisPolicy
from services.llm_backtest import ModelUnavailable, ProductionRules, run_llm_backtest
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

    def test_stored_quotes_take_precedence(self) -> None:
        for quote_at, bid, ask in (("2026-03-02T20:00:00+00:00", 9.8, 10.2), ("2026-03-03T20:00:00+00:00", 12.0, 12.4)):
            record_historical_option_quote({"option_symbol": CONTRACT["symbol"], "quote_at": quote_at, "bid": bid, "ask": ask},
                                           provider="fixture", db_path=self.db_path)
        self.assertEqual(self.backtest()["pricing_source"], "observed_quote")

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


class RecommendationEligibilityTests(unittest.TestCase):
    def test_recorded_decisions_skip_the_admission_check(self) -> None:
        view = {"decision": "call", "confidence": "high", "strategist_recommendation": {"decision": "trade_candidate"},
                "entry_gate": {"allowed": True}, "market_regime_gate": {"allowed": True}}
        with patch("services.order_candidates.catalyst_admission_allowed", return_value=False):
            self.assertTrue(OrderCandidateBuilder().recommendation_allows_entry(view))


if __name__ == "__main__":
    unittest.main()
