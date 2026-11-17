"""The stock ATR behind volatility-scaled exits, read from Alpaca daily bars."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from alpaca.data.enums import DataFeed

from trading_support import _option_positions_market as market


class UnderlyingAtrTests(unittest.TestCase):
    def test_the_atr_asks_for_iex_bars_which_the_free_data_plan_allows(self) -> None:
        # 2026-11-15: without a feed Alpaca defaults to SIP, and "recent SIP data" is refused on the free plan.
        requests = []
        bars = [{"high": 101.0, "low": 99.0, "close": 100.0}, {"high": 102.0, "low": 98.0, "close": 100.0}]
        client = SimpleNamespace(get_stock_bars=lambda request: requests.append(request) or SimpleNamespace(data={"GM": bars}))
        self.assertAlmostEqual(market._get_underlying_atr_pct("GM", lambda: client), 0.04)
        self.assertEqual(requests[0].feed, DataFeed.IEX)

    def test_a_failed_bars_request_is_logged_instead_of_silently_disabling_scaling(self) -> None:
        def refuse(request):
            raise RuntimeError("subscription does not permit querying recent SIP data")
        with self.assertLogs(market.LOGGER, level="WARNING") as logs:
            self.assertIsNone(market._get_underlying_atr_pct("GM", lambda: SimpleNamespace(get_stock_bars=refuse)))
        self.assertIn("recent SIP data", logs.output[0])


if __name__ == "__main__":
    unittest.main()
