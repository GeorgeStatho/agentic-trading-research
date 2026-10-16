from __future__ import annotations

from contextlib import closing
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "Data"

from db_common import get_connection  # noqa: E402
from market_db import import_alpaca_option_quote_csv, initialize_database, run_backtest  # noqa: E402


class AlpacaQuoteCsvImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "quotes.db"
        self.csv_path = Path(self.temp_dir.name) / "alpaca_quotes.csv"
        initialize_database(db_path=self.db_path, schema_path=DATA_DIR / "market_schema.sql")
        self.csv_path.write_text(
            "symbol,timestamp,bid_exchange,bid_price,bid_size,ask_exchange,ask_price,ask_size,conditions,tape\n"
            "O:MSFT260117C00400000,2026-01-01T10:01:00.123456789Z,Q,1.00,10,Q,1.10,12,[R],C\n"
            "O:MSFT260117C00400000,2026-01-01T10:05:00Z,Q,1.50,10,Q,1.60,12,[R],C\n"
            "O:MSFT260117C00400000,2026-01-01T10:05:00Z,Q,1.50,10,Q,1.60,12,[R],C\n"
            "O:MSFT260117C00400000,not-a-date,Q,1.20,10,Q,1.30,12,[R],C\n"
            "O:MSFT260117C00400000,2026-01-01T10:06:00Z,Q,0,10,Q,1.20,12,[R],C\n",
            encoding="utf-8",
        )
        with closing(get_connection(self.db_path)) as conn, conn:
            conn.execute(
                """
                INSERT INTO manager_decision_history (
                    symbol, decision_run_at, manager_decision, selected_option_symbol
                ) VALUES ('MSFT', '2026-01-01T10:00:00+00:00', 'call', 'MSFT260117C00400000')
                """
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_imports_alpaca_quote_csv_idempotently_and_enables_bid_ask_backtests(self) -> None:
        result = import_alpaca_option_quote_csv(self.csv_path, db_path=self.db_path, batch_size=1)
        replay_result = import_alpaca_option_quote_csv(self.csv_path, db_path=self.db_path)

        self.assertEqual(result["processed_rows"], 5)
        self.assertEqual(result["valid_rows"], 3)
        self.assertEqual(result["inserted_rows"], 2)
        self.assertEqual(result["duplicate_rows"], 1)
        self.assertEqual(result["skipped_rows"], 2)
        self.assertEqual(result["first_quote_at"], "2026-01-01T10:01:00.123456+00:00")
        self.assertEqual(replay_result["inserted_rows"], 0)
        self.assertEqual(replay_result["duplicate_rows"], 3)

        result = run_backtest(
            config={"entry_slippage_bps": 0, "exit_slippage_bps": 0, "commission_per_contract": 0},
            db_path=self.db_path,
        )
        self.assertEqual(result["events"][0]["status"], "simulated_closed")
        self.assertEqual(result["events"][0]["entry_price"], 1.1)
        self.assertEqual(result["events"][0]["exit_price"], 1.5)


if __name__ == "__main__":
    unittest.main()
