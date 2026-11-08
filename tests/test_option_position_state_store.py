"""The per-position exit state file and the option premium momentum signal."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from services.core.trading_config import OptionPriceMomentumConfig
from services.options.option_momentum import MOMENTUM_BAD, MOMENTUM_GOOD, MOMENTUM_UNKNOWN
from services.options.option_position_state import (
    delete_position_state,
    get_position_state,
    load_state,
    update_position_state,
)
from trading_support._option_positions_momentum import MOMENTUM_MIXED, evaluate_option_price_momentum


OPTION_SYMBOL = "AAPL260116C00200000"
OTHER = "MSFT260116P00300000"
MOMENTUM = OptionPriceMomentumConfig(bad_giveback_threshold=0.20, mixed_giveback_threshold=0.10,
                                     good_profit_threshold=0.15)


class OptionPositionStateStoreTests(unittest.TestCase):
    def setUp(self):
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.path = Path(temp_dir.name) / "state.json"

    def test_an_unreadable_state_file_reads_as_empty(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual({}, load_state(self.path)["positions"])

        self.path.write_text("[]", encoding="utf-8")
        self.assertEqual({}, load_state(self.path)["positions"])

    def test_malformed_position_entries_are_skipped(self):
        self.path.write_text(json.dumps({"positions": {" ": {"a": 1}, "aapl260116c00200000": {"a": 2},
                                                       OTHER: "not a dict"}}), encoding="utf-8")

        self.assertEqual({OPTION_SYMBOL: {"a": 2}}, load_state(self.path)["positions"])

    def test_a_blank_symbol_has_no_state_and_cannot_be_written(self):
        self.assertIsNone(get_position_state(self.path, " "))
        self.assertFalse(delete_position_state(self.path, ""))
        with self.assertRaises(ValueError):
            update_position_state(self.path, " ", {"a": 1})
        with self.assertRaises(TypeError):
            update_position_state(self.path, OPTION_SYMBOL, ["not", "a", "dict"])

    def test_update_keeps_the_first_opened_at_and_delete_reports_whether_it_removed_anything(self):
        first = update_position_state(self.path, OPTION_SYMBOL, {"last_action": "hold"})
        second = update_position_state(self.path, OPTION_SYMBOL, {"last_action": "sell_full"})

        self.assertEqual(first["opened_at"], second["opened_at"])
        self.assertEqual("sell_full", get_position_state(self.path, OPTION_SYMBOL)["last_action"])
        self.assertTrue(delete_position_state(self.path, OPTION_SYMBOL))
        self.assertFalse(delete_position_state(self.path, OPTION_SYMBOL))


class OptionPriceMomentumTests(unittest.TestCase):
    def evaluate(self, entry, mid, pnl=None, peak=None):
        return evaluate_option_price_momentum(entry_option_price=entry, current_option_mid_price=mid,
                                              unrealized_pl_ratio=pnl, max_pnl_pct=peak, config=MOMENTUM)

    def test_missing_prices_leave_momentum_unknown(self):
        result = self.evaluate(None, 0)

        self.assertEqual(MOMENTUM_UNKNOWN, result["status"])
        self.assertEqual(3, len(result["reasons"]))

    def test_status_follows_the_return_and_the_giveback_from_peak(self):
        cases = {
            "gave back past the bad threshold": ((1.0, 1.10, None, 0.40), MOMENTUM_BAD),
            "below entry": ((1.0, 0.90, None, None), MOMENTUM_BAD),
            "gave back past the mixed threshold": ((1.0, 1.25, None, 0.37), MOMENTUM_MIXED),
            "comfortably up near the peak": ((1.0, 1.20, None, 0.22), MOMENTUM_GOOD),
            "flat near entry": ((1.0, 1.05, None, None), MOMENTUM_MIXED),
        }
        for name, (args, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(expected, self.evaluate(*args)["status"])

    def test_reported_profit_takes_precedence_over_the_price_derived_return(self):
        result = self.evaluate(1.0, 1.05, pnl=0.30, peak=0.32)

        self.assertEqual(0.30, result["current_pnl_ratio"])
        self.assertEqual(MOMENTUM_GOOD, result["status"])


if __name__ == "__main__":
    unittest.main()
