from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from db_helpers import get_connection, initialize_market_database
from market_db_modules.queries import list_option_trade_executions


class ExecutedTradesTests(unittest.TestCase):
    def test_executed_trades_query_excludes_orders_older_than_thirty_days(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "executed-trades.sqlite3"
            initialize_market_database(db_path=db_path)
            now = datetime.now(timezone.utc)
            with get_connection(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO option_trade_executions (
                        order_id, underlying_symbol, option_symbol, submitted_at
                    ) VALUES ('fresh-order', 'AAPL', 'AAPL260821C00180000', ?)
                    """,
                    ((now - timedelta(days=29)).isoformat(),),
                )
                conn.execute(
                    """
                    INSERT INTO option_trade_executions (
                        order_id, underlying_symbol, option_symbol, submitted_at
                    ) VALUES ('stale-order', 'AAPL', 'AAPL260718C00180000', ?)
                    """,
                    ((now - timedelta(days=31)).isoformat(),),
                )

            rows = list_option_trade_executions(within_days=30, db_path=db_path)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["order_id"], "fresh-order")


if __name__ == "__main__":
    unittest.main()
