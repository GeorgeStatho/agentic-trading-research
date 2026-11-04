from __future__ import annotations

import unittest

try:
    from api_support.dashboard import total_unrealized_pl
    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    total_unrealized_pl = None
    FLASK_AVAILABLE = False


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this environment.")
class TotalUnrealizedPlTests(unittest.TestCase):
    def test_sums_the_broker_figure_across_open_positions(self):
        # Alpaca returns money as strings.
        positions = [{"symbol": "ORCL261204P00123000", "unrealized_pl": "-35.5"}, {"symbol": "SPY", "unrealized_pl": "12.25"}]
        self.assertEqual(total_unrealized_pl(positions), -23.25)

    def test_no_open_positions_means_nothing_unrealized(self):
        self.assertEqual(total_unrealized_pl([]), 0.0)

    def test_unknown_when_the_broker_sent_no_usable_figure(self):
        self.assertIsNone(total_unrealized_pl([{"symbol": "SPY", "unrealized_pl": None}]))

    def test_a_position_missing_its_figure_makes_the_total_unknown(self):
        # A partial sum would read as the whole book.
        self.assertIsNone(total_unrealized_pl([{"unrealized_pl": "10"}, {"unrealized_pl": ""}]))


if __name__ == "__main__":
    unittest.main()
