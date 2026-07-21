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
from market_db_modules.backtesting import _apply_portfolio_limits, _merged_config, _metrics  # noqa: E402


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


class PortfolioGuardrailTests(unittest.TestCase):
    @staticmethod
    def _event(
        identifier: int,
        *,
        entry_at: str,
        exit_at: str,
        sector: str = "technology",
        entry_notional: float = 1000.0,
        realized_pnl_dollars: float = 0.0,
    ) -> dict:
        return {
            "manager_decision_history_id": identifier,
            "status": "simulated_closed",
            "entry_at": entry_at,
            "exit_at": exit_at,
            "risk_release_at": exit_at,
            "portfolio_sector": sector,
            "entry_notional": entry_notional,
            "realized_pnl_dollars": realized_pnl_dollars,
            "realized_pnl_pct": realized_pnl_dollars / entry_notional * 100,
            "warnings": [],
        }

    def _run_limits(self, events: list[dict], **overrides) -> list[dict]:
        config = _merged_config({
            "max_concurrent_positions": 10,
            "max_total_entry_notional": 100000,
            "max_sector_notional_pct": 100,
            "daily_loss_limit_dollars": 100000,
            **overrides,
        })
        return _apply_portfolio_limits(events, config)

    def test_max_concurrent_positions_rejects_overlapping_entry(self) -> None:
        events = self._run_limits([
            self._event(1, entry_at="2026-01-01T10:00:00+00:00", exit_at="2026-01-01T11:00:00+00:00"),
            self._event(2, entry_at="2026-01-01T10:01:00+00:00", exit_at="2026-01-01T11:01:00+00:00"),
        ], max_concurrent_positions=1)
        self.assertEqual(events[0]["status"], "simulated_closed")
        self.assertEqual(events[1]["warnings"], ["portfolio_max_concurrent_positions"])

    def test_total_notional_and_sector_limits_are_auditable(self) -> None:
        total_events = self._run_limits([
            self._event(1, entry_at="2026-01-01T10:00:00+00:00", exit_at="2026-01-01T11:00:00+00:00"),
            self._event(2, entry_at="2026-01-01T10:01:00+00:00", exit_at="2026-01-01T11:01:00+00:00"),
        ], max_total_entry_notional=1500)
        self.assertEqual(total_events[1]["warnings"], ["portfolio_total_entry_notional_limit"])

        sector_events = self._run_limits([
            self._event(1, entry_at="2026-01-01T10:00:00+00:00", exit_at="2026-01-01T11:00:00+00:00"),
            self._event(2, entry_at="2026-01-01T10:01:00+00:00", exit_at="2026-01-01T11:01:00+00:00"),
        ], max_total_entry_notional=5000, max_sector_notional_pct=30)
        self.assertEqual(sector_events[1]["warnings"], ["portfolio_sector_notional_limit"])

    def test_daily_loss_limit_blocks_later_entries_and_counts_rejections(self) -> None:
        events = self._run_limits([
            self._event(1, entry_at="2026-01-01T08:00:00+00:00", exit_at="2026-01-01T09:00:00+00:00", realized_pnl_dollars=-200),
            self._event(2, entry_at="2026-01-01T10:00:00+00:00", exit_at="2026-01-01T11:00:00+00:00"),
        ], daily_loss_limit_dollars=150)
        self.assertEqual(events[1]["warnings"], ["portfolio_daily_loss_limit"])
        self.assertEqual(_metrics(events)["portfolio_rejected_count"], 1)


if __name__ == "__main__":
    unittest.main()
