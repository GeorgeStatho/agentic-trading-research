from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

import performance_metrics  # noqa: E402


class PerformanceMetricsTests(unittest.TestCase):
    def test_normalize_equity_curve_and_returns_support_sharpe_inputs(self) -> None:
        portfolio_history = {
            "timestamp": [1710000000, 1710086400, 1710172800, 1710259200],
            "equity": [100000, 101000, 100500, 102000],
        }

        equity_curve = performance_metrics.normalize_equity_curve(portfolio_history)
        daily_returns = performance_metrics.compute_daily_returns(equity_curve)
        sharpe = performance_metrics.compute_sharpe_metrics(daily_returns, annual_risk_free_rate=0.0)
        sortino = performance_metrics.compute_sortino_metrics(daily_returns, annual_risk_free_rate=0.0)

        self.assertEqual(len(equity_curve), 4)
        self.assertEqual(len(daily_returns), 3)
        self.assertAlmostEqual(performance_metrics.compute_total_return_pct(equity_curve), 2.0)
        self.assertAlmostEqual(performance_metrics.compute_max_drawdown_pct(equity_curve), 0.4950495, places=4)
        self.assertIsNotNone(sharpe["annualized_sharpe"])
        self.assertIsNotNone(sortino["annualized_sortino"])

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
