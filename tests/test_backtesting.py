from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
DATA_DIR = ROOT_DIR / "Data"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from db_common import get_connection  # noqa: E402
from db_helpers import initialize_news_database  # noqa: E402
from market_db import (  # noqa: E402
    get_backtest_run,
    initialize_database,
    record_historical_option_quote,
    run_backtest,
)


class BacktestingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "backtest.db"
        initialize_database(db_path=self.db_path, schema_path=DATA_DIR / "market_schema.sql")
        initialize_news_database(db_path=self.db_path, schema_path=DATA_DIR / "news_schema.sql")
        with get_connection(self.db_path) as conn:
            self.article_id = conn.execute(
                """
                INSERT INTO news_articles (
                    source, article_key, title, source_url, published_at, created_at
                ) VALUES ('Test Wire', 'future-ingestion', 'Future ingestion', 'https://example.test/future',
                          '2026-01-01T09:00:00+00:00', '2026-01-01T12:00:00+00:00')
                """
            ).lastrowid
            self.decision_id = conn.execute(
                """
                INSERT INTO manager_decision_history (
                    symbol, decision_run_at, manager_decision, manager_reason,
                    selected_option_symbol, manager_input_json
                ) VALUES ('MSFT', '2026-01-01T10:00:00+00:00', 'call', 'Stored evidence supported the setup.',
                          'MSFT260117C00400000', ?)
                """,
                (json.dumps({"article_references": [{"article_id": self.article_id, "title": "Future ingestion"}]}),),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO manager_decision_history (
                    symbol, decision_run_at, manager_decision, selected_option_symbol
                ) VALUES ('AAPL', '2026-01-01T10:00:00+00:00', 'put', 'AAPL260117P00200000')
                """
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_backtest_uses_available_evidence_and_historical_bid_ask_fills_only(self) -> None:
        first_quote_id = record_historical_option_quote(
            {"option_symbol": "MSFT260117C00400000", "quote_at": "2026-01-01T10:01:00+00:00", "bid": 1.0, "ask": 1.1, "volume": 20},
            provider="fixture",
            db_path=self.db_path,
        )
        duplicate_quote_id = record_historical_option_quote(
            {"option_symbol": "MSFT260117C00400000", "quote_at": "2026-01-01T10:01:00+00:00", "bid": 8.0, "ask": 9.0, "volume": 20},
            provider="fixture",
            db_path=self.db_path,
        )
        record_historical_option_quote(
            {"option_symbol": "MSFT260117C00400000", "quote_at": "2026-01-01T10:05:00+00:00", "bid": 1.5, "ask": 1.6, "volume": 20},
            provider="fixture",
            db_path=self.db_path,
        )

        result = run_backtest(
            config={"entry_slippage_bps": 0, "exit_slippage_bps": 0, "commission_per_contract": 0, "max_hold_hours": 24},
            db_path=self.db_path,
        )

        self.assertEqual(first_quote_id, duplicate_quote_id)
        msft_event = next(event for event in result["events"] if event["symbol"] == "MSFT")
        aapl_event = next(event for event in result["events"] if event["symbol"] == "AAPL")
        self.assertEqual(msft_event["status"], "simulated_closed")
        self.assertEqual(msft_event["entry_price"], 1.1)
        self.assertEqual(msft_event["exit_price"], 1.5)
        self.assertAlmostEqual(msft_event["realized_pnl_pct"], 36.3636, places=3)
        self.assertEqual(aapl_event["status"], "no_fill")
        self.assertEqual(result["run"]["metrics"]["filled_count"], 1)
        self.assertEqual(result["run"]["metrics"]["no_fill_count"], 1)
        with get_connection(self.db_path) as conn:
            stored_quote = conn.execute("SELECT ask_price FROM historical_option_quotes WHERE id = ?", (first_quote_id,)).fetchone()
            snapshot = conn.execute("SELECT snapshot_json FROM decision_evidence_snapshots WHERE manager_decision_history_id = ?", (self.decision_id,)).fetchone()
            execution_count = conn.execute("SELECT COUNT(*) FROM option_trade_executions").fetchone()[0]
        self.assertEqual(stored_quote["ask_price"], 1.1)
        self.assertEqual(json.loads(snapshot["snapshot_json"])["articles"], [])
        self.assertEqual(execution_count, 0)

    def test_backtest_rejects_an_entry_quote_that_is_too_delayed(self) -> None:
        record_historical_option_quote(
            {"option_symbol": "MSFT260117C00400000", "quote_at": "2026-01-01T11:00:00+00:00", "bid": 1.0, "ask": 1.1, "volume": 20},
            provider="fixture",
            db_path=self.db_path,
        )
        result = run_backtest(config={"max_entry_delay_minutes": 5}, db_path=self.db_path)
        event = next(item for item in result["events"] if item["symbol"] == "MSFT")
        self.assertEqual(event["status"], "no_fill")
        self.assertIn("next_quote_exceeds_entry_delay", event["warnings"])
        loaded = get_backtest_run(result["run"]["id"], db_path=self.db_path)
        self.assertTrue(loaded["read_only"])


if __name__ == "__main__":
    unittest.main()
