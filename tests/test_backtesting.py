from __future__ import annotations

from contextlib import closing
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "Data"

from db_common import get_connection  # noqa: E402
from db_helpers import initialize_news_database  # noqa: E402
from market_db import (  # noqa: E402
    get_backtest_run,
    initialize_database,
    record_historical_option_quote,
    run_backtest,
    simulate_backtest_metrics,
)
from market_db_modules.backtesting import _apply_portfolio_limits, _merged_config, _metrics  # noqa: E402


class BacktestingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "backtest.db"
        initialize_database(db_path=self.db_path, schema_path=DATA_DIR / "market_schema.sql")
        initialize_news_database(db_path=self.db_path, schema_path=DATA_DIR / "news_schema.sql")
        with closing(get_connection(self.db_path)) as conn, conn:
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
                    symbol, decision_run_at, manager_decision, manager_reason, selected_option_symbol
                ) VALUES ('MSFT', '2026-01-01T10:00:00+00:00', 'call', 'Stored evidence supported the setup.',
                          'MSFT260117C00400000')
                """
            ).lastrowid
            conn.execute(
                "INSERT INTO manager_decision_payloads (manager_decision_history_id, manager_input_json) VALUES (?, ?)",
                (
                    self.decision_id,
                    json.dumps({"article_references": [{"article_id": self.article_id, "title": "Future ingestion"}]}),
                ),
            )
            conn.execute(
                """
                INSERT INTO manager_decision_history (
                    symbol, decision_run_at, manager_decision, selected_option_symbol
                ) VALUES ('AAPL', '2026-01-01T10:00:00+00:00', 'put', 'AAPL260117P00200000')
                """
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_all_history_preserves_older_priced_trades_and_includes_latest_decisions(self) -> None:
        for timestamp, bid, ask in (
            ("2026-01-01T10:01:00+00:00", 1.0, 1.1),
            ("2026-01-01T10:05:00+00:00", 1.5, 1.6),
        ):
            record_historical_option_quote(
                {"option_symbol": "MSFT260117C00400000", "quote_at": timestamp, "bid": bid, "ask": ask},
                provider="fixture", db_path=self.db_path,
            )
        earlier = run_backtest(db_path=self.db_path)
        self.assertEqual(earlier["run"]["metrics"]["filled_count"], 1)
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.executemany(
                "INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision) VALUES ('NVDA', ?, 'call')",
                [((datetime(2026, 9, 20, tzinfo=timezone.utc) + timedelta(minutes=i)).isoformat(),) for i in range(1001)],
            )
        recent = run_backtest(limit=700, db_path=self.db_path)
        self.assertIsNone(recent["run"]["metrics"]["expectancy_pct"])
        complete = run_backtest(limit=None, db_path=self.db_path)
        metrics = complete["run"]["metrics"]
        self.assertEqual(metrics["decision_count"], 1003)
        self.assertEqual(metrics["available_decision_count"], 1003)
        self.assertEqual(metrics["omitted_older_decision_count"], 0)
        self.assertIsNone(metrics["decision_limit"])
        self.assertEqual(metrics["filled_count"], 1)
        self.assertEqual(metrics["expectancy_pct"], earlier["run"]["metrics"]["expectancy_pct"])
        self.assertTrue(complete["events"][-1]["as_of"].startswith("2026-09-20"))
        self.assertEqual(get_backtest_run(earlier["run"]["id"], db_path=self.db_path), earlier)

    def test_default_window_includes_latest_decisions_past_250_and_preserves_old_runs(self) -> None:
        old_run = run_backtest(db_path=self.db_path)
        start = datetime(2026, 9, 20, 10, tzinfo=timezone.utc)
        timestamps = [(start + timedelta(minutes=i)).isoformat() for i in range(251)]
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.executemany(
                "INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision) VALUES ('NVDA', ?, 'call')",
                [(timestamp,) for timestamp in timestamps],
            )
        result = run_backtest(db_path=self.db_path)
        self.assertEqual([event["as_of"] for event in result["events"]], timestamps[1:])
        metrics = result["run"]["metrics"]
        self.assertEqual(metrics["available_decision_count"], 253)
        self.assertEqual(metrics["decision_count"], 250)
        self.assertEqual(metrics["omitted_older_decision_count"], 3)
        self.assertEqual(metrics["decision_start_at"], timestamps[1])
        self.assertEqual(metrics["decision_end_at"], timestamps[-1])
        self.assertEqual(get_backtest_run(old_run["run"]["id"], db_path=self.db_path), old_run)

    def test_latest_selection_respects_date_bounds_and_id_ties(self) -> None:
        timestamp = "2026-09-20T10:00:00+00:00"
        with closing(get_connection(self.db_path)) as conn, conn:
            ids = [conn.execute(
                "INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision) VALUES (?, ?, 'call')",
                (symbol, timestamp),
            ).lastrowid for symbol in ("NVDA", "AMD", "MU")]
            conn.execute(
                "INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision) VALUES ('IBM', '2026-09-21T10:00:00+00:00', 'call')"
            )
        result = run_backtest(start_at=timestamp, end_at=timestamp, limit=2, db_path=self.db_path)
        self.assertEqual([event["manager_decision_history_id"] for event in result["events"]], ids[1:])
        self.assertEqual(result["run"]["metrics"]["available_decision_count"], 3)
        self.assertEqual(result["run"]["metrics"]["omitted_older_decision_count"], 1)
        complete = run_backtest(start_at=timestamp, end_at=timestamp, limit=None, db_path=self.db_path)
        self.assertEqual([event["manager_decision_history_id"] for event in complete["events"]], ids)
        self.assertEqual(complete["run"]["metrics"]["omitted_older_decision_count"], 0)

    def test_empty_date_window_reports_empty_coverage(self) -> None:
        result = run_backtest(start_at="2027-01-01T00:00:00+00:00", db_path=self.db_path)
        self.assertEqual(result["events"], [])
        self.assertEqual(result["run"]["metrics"]["available_decision_count"], 0)
        self.assertIsNone(result["run"]["metrics"]["decision_end_at"])

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
        with closing(get_connection(self.db_path)) as conn, conn:
            stored_quote = conn.execute("SELECT ask_price FROM historical_option_quotes WHERE id = ?", (first_quote_id,)).fetchone()
            snapshot = conn.execute("SELECT snapshot_json FROM decision_evidence_snapshots WHERE manager_decision_history_id = ?", (self.decision_id,)).fetchone()
            execution_count = conn.execute("SELECT COUNT(*) FROM option_trade_executions").fetchone()[0]
        self.assertEqual(stored_quote["ask_price"], 1.1)
        self.assertEqual(json.loads(snapshot["snapshot_json"])["articles"], [])
        self.assertEqual(execution_count, 0)

    def test_simulated_metrics_match_a_recorded_run_without_recording_one(self) -> None:
        for timestamp, bid, ask in (
            ("2026-01-01T10:01:00+00:00", 1.0, 1.1),
            ("2026-01-01T10:05:00+00:00", 1.5, 1.6),
        ):
            record_historical_option_quote(
                {"option_symbol": "MSFT260117C00400000", "quote_at": timestamp, "bid": bid, "ask": ask},
                provider="fixture", db_path=self.db_path,
            )
        config = {"take_profit_pct": 20.0}
        simulated = simulate_backtest_metrics(config=config, db_path=self.db_path)
        with closing(get_connection(self.db_path)) as conn, conn:
            run_count = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]
        recorded = run_backtest(config=config, limit=None, db_path=self.db_path)

        self.assertEqual(run_count, 0)
        self.assertEqual(simulated, recorded["run"]["metrics"])

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
