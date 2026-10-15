"""Direction scoring: replayed answers graded by the stock's later closes, with no option pricing."""
from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from db_common import get_connection
from db_helpers import initialize_news_database
from market_db import initialize_database, record_historical_underlying_bar
from services import llm_direction_score
from services.llm_backtest import ReplayRun
from services.llm_direction_score import run_direction_score, summarize

DATA_DIR = Path(__file__).resolve().parents[1] / "Data"
DECIDED_AT = "2026-03-02T15:00:00+00:00"
TRADING_DAYS = [date(2026, 3, 2) + timedelta(days=offset) for offset in range(14)
                if (date(2026, 3, 2) + timedelta(days=offset)).weekday() < 5]


class DirectionScoreFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "direction.db"
        initialize_database(db_path=self.db_path, schema_path=DATA_DIR / "market_schema.sql")
        initialize_news_database(db_path=self.db_path, schema_path=DATA_DIR / "news_schema.sql")

    def add_decision(self, at: str = DECIDED_AT, *, frozen: bool = True, production: str = "neither") -> None:
        manager_input = {"replay_input": {"company": {"symbol": "MSFT"},
                                          "market_context": {"current_stock_price": {"price": 100.0}}}} if frozen else {}
        with closing(get_connection(self.db_path)) as conn, conn:
            history_id = conn.execute("INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision) "
                                      "VALUES ('MSFT', ?, ?)", (at, production)).lastrowid
            conn.execute("INSERT INTO manager_decision_payloads (manager_decision_history_id, manager_input_json, "
                         "manager_output_json) VALUES (?, ?, ?)",
                         (history_id, json.dumps(manager_input), json.dumps({"recommendation": {"decision": production}})))

    def add_rising_closes(self, days: int = len(TRADING_DAYS)) -> None:
        # Close n (1-based) is 100 + n, so the 5th close is +5% and the 10th +10% from the 100 the model saw.
        for number, day in enumerate(TRADING_DAYS[:days], start=1):
            record_historical_underlying_bar({"symbol": "MSFT", "bar_at": f"{day.isoformat()}T05:00:00+00:00",
                                              "close": 100.0 + number}, provider="fixture", db_path=self.db_path)

    def score(self, decision: str, **options) -> dict:
        answer = Mock(return_value={"recommendation": {"decision": decision, "confidence": "high"}})
        return run_direction_score(model="local", decide=answer, db_path=self.db_path, **options)


class ScoringTests(DirectionScoreFixture):
    def test_a_call_scores_the_stock_return(self) -> None:
        self.add_decision()
        self.add_rising_closes()
        self.assertEqual(self.score("call")["decisions"][0]["signed_return_pct"], {5: 5.0, 10: 10.0})

    def test_a_put_scores_the_opposite_return(self) -> None:
        self.add_decision()
        self.add_rising_closes()
        self.assertEqual(self.score("put")["decisions"][0]["signed_return_pct"], {5: -5.0, 10: -10.0})

    def test_neither_is_counted_but_not_scored(self) -> None:
        self.add_decision()
        self.add_rising_closes()
        score = self.score("neither")["score"]
        self.assertEqual((score["directional"]["count"], score["neither_count"]), (0, 1))

    def test_too_few_later_closes_are_reported(self) -> None:
        self.add_decision()
        self.add_rising_closes(days=9)
        self.assertEqual(self.score("call")["replay"]["outcomes"], {"no_forward_prices": 1})

    def test_stored_answers_are_production_decisions(self) -> None:
        self.add_decision(production="call")
        self.add_rising_closes()
        result = run_direction_score(model="stored", db_path=self.db_path)
        self.assertEqual(result["decisions"][0]["decision"], "call")


class SummaryTests(unittest.TestCase):
    def row(self, decision: str, move: float, confidence: str = "high") -> dict:
        sign = {"call": 1, "put": -1}.get(decision)
        return {"decision": decision, "production_decision": "call", "confidence": confidence,
                "stock_return_pct": {5: move, 10: move},
                "signed_return_pct": {5: sign * move, 10: sign * move} if sign else None}

    def test_hit_rate_counts_moves_in_the_called_direction(self) -> None:
        summary = summarize([self.row("call", 2.0), self.row("put", 1.0), self.row("neither", 3.0)])
        self.assertEqual(summary["directional"]["5d"]["hit_rate_pct"], 50.0)

    def test_unconditional_moves_include_every_scored_decision(self) -> None:
        summary = summarize([self.row("call", 2.0), self.row("neither", -1.0)])
        self.assertEqual(summary["unconditional_stock_moves"]["count"], 2)

    def test_results_are_split_by_confidence(self) -> None:
        summary = summarize([self.row("call", 2.0, "high"), self.row("call", -2.0, "medium")])
        self.assertEqual(sorted(summary["directional"]["by_confidence"]), ["high", "medium"])


class ReplayWindowTests(DirectionScoreFixture):
    def test_decisions_without_frozen_input_do_not_take_a_spacing_slot(self) -> None:
        self.add_decision("2026-03-02T14:00:00+00:00", frozen=False)
        self.add_decision("2026-03-02T15:00:00+00:00")
        run = ReplayRun.build(model="stored", min_hours_between=4, db_path=self.db_path)
        self.assertEqual([decision["decision_run_at"] for decision in run.decisions], ["2026-03-02T15:00:00+00:00"])

    def test_history_is_fetched_only_for_symbols_without_later_closes(self) -> None:
        self.add_decision()
        run = ReplayRun.build(model="stored", db_path=self.db_path)
        with patch.object(llm_direction_score, "store_daily_history", return_value={}) as store:
            llm_direction_score.fetch_missing_history(run, db_path=self.db_path)
        self.assertEqual(store.call_args.args[0], {"MSFT"})


if __name__ == "__main__":
    unittest.main()
