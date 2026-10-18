"""Direction scoring: replayed answers graded by the stock's later closes, with no option pricing."""
from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from db_common import get_connection
from news_db_modules.bootstrap import initialize_database as initialize_news_database
from market_db_modules.bootstrap import initialize_database
from market_db_modules.backtesting import record_historical_underlying_bar
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


class SummaryTests(unittest.TestCase):
    def row(self, decision: str, move: float, confidence: str = "high") -> dict:
        sign = {"call": 1, "put": -1}.get(decision)
        return {"decision": decision, "production_decision": "call", "confidence": confidence,
                "stock_return_pct": {5: move, 10: move},
                "signed_return_pct": {5: sign * move, 10: sign * move} if sign else None}

    def test_hit_rate_counts_moves_in_the_called_direction(self) -> None:
        summary = summarize([self.row("call", 2.0), self.row("put", 1.0), self.row("neither", 3.0)])
        self.assertEqual(summary["directional"]["5d"]["hit_rate_pct"], 50.0)


if __name__ == "__main__":
    unittest.main()
