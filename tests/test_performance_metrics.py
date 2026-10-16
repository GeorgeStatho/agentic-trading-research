from __future__ import annotations

import unittest

import performance_metrics  # noqa: E402


class PerformanceMetricsTests(unittest.TestCase):
    def test_compute_trade_metrics_calculates_win_rate_profit_factor_and_average_returns(self) -> None:
        fills = [
            {
                "transaction_time": "2026-05-01T14:30:00Z",
                "symbol": "AAPL",
                "side": "buy",
                "qty": "1",
                "price": "100",
            },
            {
                "transaction_time": "2026-05-02T14:30:00Z",
                "symbol": "AAPL",
                "side": "sell",
                "qty": "1",
                "price": "110",
            },
            {
                "transaction_time": "2026-05-03T14:30:00Z",
                "symbol": "MSFT",
                "side": "buy",
                "qty": "1",
                "price": "100",
            },
            {
                "transaction_time": "2026-05-04T14:30:00Z",
                "symbol": "MSFT",
                "side": "sell",
                "qty": "1",
                "price": "95",
            },
        ]

        metrics = performance_metrics.compute_trade_metrics(fills)

        self.assertEqual(metrics["wins"], 1)
        self.assertEqual(metrics["closed_trades"], 2)
        self.assertEqual(metrics["win_rate_pct"], 50.0)
        self.assertEqual(metrics["gross_profit"], 10.0)
        self.assertEqual(metrics["gross_loss"], 5.0)
        self.assertEqual(metrics["profit_factor"], 2.0)
        self.assertEqual(metrics["average_trade_return_pct"], 2.5)
        self.assertEqual(metrics["average_win_return_pct"], 10.0)
        self.assertEqual(metrics["average_loss_return_pct"], -5.0)


if __name__ == "__main__":
    unittest.main()
