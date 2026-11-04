from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db_common import use_database
from market_db_modules.bootstrap import initialize_database
from market_db_modules.trade_executions import record_option_trade_execution

try:
    from api_support.trades import build_executed_trades_payload
    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    build_executed_trades_payload = None
    FLASK_AVAILABLE = False


def _record(db_path: Path, order_id: str, option_symbol: str, side: str, status: str, hours_ago: float, fill_price: float | None) -> None:
    submitted_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    record_option_trade_execution(
        {
            "submitted": True,
            "symbol": option_symbol[:4].rstrip("0123456789"),
            "decision": "put",
            "order_qty": 1,
            "selected_option_symbol": option_symbol,
            "order": {"id": order_id, "symbol": option_symbol, "side": side, "status": status, "qty": 1, "submitted_at": submitted_at},
        },
        paper=True,
        db_path=db_path,
    )
    if fill_price is not None:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("UPDATE option_trade_executions SET fill_price = ?, filled_qty = 1 WHERE order_id = ?", (fill_price, order_id))
            conn.commit()
        finally:
            conn.close()


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class ExecutedTradesPayloadTests(unittest.TestCase):
    def payload(self, rows):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "trades.sqlite3"
            initialize_database(db_path=db_path)
            for row in rows:
                _record(db_path, *row)
            with use_database(db_path):
                return {trade["order_id"]: trade for trade in build_executed_trades_payload()["trades"]}

    def test_each_order_carries_its_fill_price_and_filled_quantity(self):
        trades = self.payload([
            ("entry", "ORCL261120P00121000", "buy", "filled", 30, 5.9),
            ("cancel", "ORCL261120P00121000", "sell", "canceled", 5, None),
        ])

        self.assertEqual(trades["entry"]["fill_price"], 5.9)
        self.assertEqual(trades["entry"]["filled_qty"], 1)
        self.assertIsNone(trades["cancel"]["fill_price"])

    def test_exits_point_at_the_entry_they_close(self):
        trades = self.payload([
            ("old-entry", "ORCL261120P00121000", "buy", "filled", 72, 5.0),
            ("old-exit", "ORCL261120P00121000", "sell", "filled", 60, 6.0),
            ("entry", "ORCL261120P00121000", "buy", "filled", 30, 5.9),
            ("cancel", "ORCL261120P00121000", "sell", "canceled", 5, None),
            ("exit", "ORCL261120P00121000", "sell", "filled", 4, 5.65),
            ("other", "TSLA261127C00415000", "buy", "filled", 3, 13.45),
        ])

        self.assertEqual(trades["old-exit"]["position_id"], "old-entry")
        self.assertEqual(trades["entry"]["position_id"], "entry")
        self.assertEqual(trades["cancel"]["position_id"], "entry")
        self.assertEqual(trades["exit"]["position_id"], "entry")
        self.assertEqual(trades["other"]["position_id"], "other")

    def test_an_exit_whose_entry_was_never_filled_or_loaded_stays_unpaired(self):
        trades = self.payload([
            ("unfilled-entry", "NVDA261030C00240000", "buy", "canceled", 10, None),
            ("exit", "NVDA261030C00240000", "sell", "filled", 5, 5.8),
        ])

        self.assertIsNone(trades["unfilled-entry"]["position_id"])
        self.assertIsNone(trades["exit"]["position_id"])


if __name__ == "__main__":
    unittest.main()
