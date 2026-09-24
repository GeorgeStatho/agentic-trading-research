from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
DATA_DIR = ROOT_DIR / "Data"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from db_helpers import get_connection, initialize_market_database
from market_db_modules.portfolio_risk import (
    count_option_entry_executions_since,
    get_latest_stop_loss_exit_at,
)


def _insert_execution(
    conn,
    *,
    order_id: str,
    underlying_symbol: str,
    order_side: str,
    recorded_at: str,
    decision: str = "call",
) -> None:
    conn.execute(
        """
        INSERT INTO option_trade_executions (
            order_id, underlying_symbol, option_symbol, decision, order_side, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            underlying_symbol,
            f"{underlying_symbol}260821C00180000",
            decision,
            order_side,
            recorded_at,
        ),
    )


class SymbolEntryAttemptLimitTests(unittest.TestCase):
    """Cover the journal queries behind the per-symbol entry attempt cap."""

    @staticmethod
    def _journal_timestamp(days_ago: float) -> str:
        # Matches SQLite CURRENT_TIMESTAMP, which is what the journal writes.
        return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")

    def test_counts_only_recent_buys_for_the_requested_symbol(self) -> None:
        # ignore_cleanup_errors: the db helpers leave their connection open for the
        # GC to close, which blocks the temp-file unlink on Windows.
        with TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "entry-attempts.sqlite3"
            initialize_market_database(db_path=db_path)
            with get_connection(db_path) as conn:
                _insert_execution(
                    conn,
                    order_id="recent-buy",
                    underlying_symbol="NVDA",
                    order_side="buy",
                    recorded_at=self._journal_timestamp(2),
                )
                _insert_execution(
                    conn,
                    order_id="second-recent-buy",
                    underlying_symbol="NVDA",
                    order_side="buy",
                    recorded_at=self._journal_timestamp(6),
                )
                _insert_execution(
                    conn,
                    order_id="old-buy",
                    underlying_symbol="NVDA",
                    order_side="buy",
                    recorded_at=self._journal_timestamp(30),
                )
                _insert_execution(
                    conn,
                    order_id="recent-sell",
                    underlying_symbol="NVDA",
                    order_side="sell",
                    decision="stop_loss",
                    recorded_at=self._journal_timestamp(1),
                )
                _insert_execution(
                    conn,
                    order_id="other-symbol-buy",
                    underlying_symbol="INTC",
                    order_side="buy",
                    recorded_at=self._journal_timestamp(1),
                )

            window_start = datetime.now(timezone.utc) - timedelta(days=7)
            self.assertEqual(
                count_option_entry_executions_since(
                    underlying_symbol="NVDA",
                    start_at=window_start,
                    db_path=db_path,
                ),
                2,
            )
            self.assertEqual(
                count_option_entry_executions_since(
                    underlying_symbol="intc",
                    start_at=window_start,
                    db_path=db_path,
                ),
                1,
            )

    def test_counts_isoformat_recorded_at_values(self) -> None:
        # Rows written through other paths can carry an ISO 'T' separator.
        # ignore_cleanup_errors: the db helpers leave their connection open for the
        # GC to close, which blocks the temp-file unlink on Windows.
        with TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "entry-attempts-iso.sqlite3"
            initialize_market_database(db_path=db_path)
            with get_connection(db_path) as conn:
                _insert_execution(
                    conn,
                    order_id="iso-buy",
                    underlying_symbol="NVDA",
                    order_side="buy",
                    recorded_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                )

            self.assertEqual(
                count_option_entry_executions_since(
                    underlying_symbol="NVDA",
                    start_at=datetime.now(timezone.utc) - timedelta(days=7),
                    db_path=db_path,
                ),
                1,
            )

    def test_returns_latest_stop_loss_exit_only(self) -> None:
        # ignore_cleanup_errors: the db helpers leave their connection open for the
        # GC to close, which blocks the temp-file unlink on Windows.
        with TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "stop-cooldown.sqlite3"
            initialize_market_database(db_path=db_path)
            newest_stop = self._journal_timestamp(3)
            with get_connection(db_path) as conn:
                _insert_execution(
                    conn,
                    order_id="old-stop",
                    underlying_symbol="NVDA",
                    order_side="sell",
                    decision="stop_loss",
                    recorded_at=self._journal_timestamp(20),
                )
                _insert_execution(
                    conn,
                    order_id="new-stop",
                    underlying_symbol="NVDA",
                    order_side="sell",
                    decision="stop_loss",
                    recorded_at=newest_stop,
                )
                _insert_execution(
                    conn,
                    order_id="profit-exit",
                    underlying_symbol="NVDA",
                    order_side="sell",
                    decision="trailing_profit_stop",
                    recorded_at=self._journal_timestamp(1),
                )

            self.assertEqual(
                get_latest_stop_loss_exit_at(underlying_symbol="NVDA", db_path=db_path),
                newest_stop,
            )

    def test_returns_none_without_a_stop_loss_exit(self) -> None:
        # ignore_cleanup_errors: the db helpers leave their connection open for the
        # GC to close, which blocks the temp-file unlink on Windows.
        with TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "stop-cooldown-empty.sqlite3"
            initialize_market_database(db_path=db_path)
            with get_connection(db_path) as conn:
                _insert_execution(
                    conn,
                    order_id="profit-exit",
                    underlying_symbol="NVDA",
                    order_side="sell",
                    decision="trailing_profit_stop",
                    recorded_at=self._journal_timestamp(1),
                )

            self.assertIsNone(get_latest_stop_loss_exit_at(underlying_symbol="NVDA", db_path=db_path))
            self.assertIsNone(get_latest_stop_loss_exit_at(underlying_symbol="", db_path=db_path))


if __name__ == "__main__":
    unittest.main()
