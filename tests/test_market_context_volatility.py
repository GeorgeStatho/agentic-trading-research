from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import agent_helpers.market_context as market_context  # noqa: E402
from services.core.settings import get_settings, use_settings


def iv_history_settings(**changes):
    """Run with a temporary IV-history location and thresholds."""
    return use_settings(manager_context=replace(get_settings().manager_context, **changes))


class MarketContextVolatilityTests(unittest.TestCase):
    def test_summarize_underlying_price_history_computes_realized_volatility_from_alpaca_bars(self):
        start_time = datetime(2026, 1, 2, tzinfo=timezone.utc)
        bars = []
        for index in range(70):
            close_price = 100.0 + (index * 0.35) + ((index % 4) - 1.5) * 0.8
            bars.append(
                {
                    "timestamp": (start_time + timedelta(days=index)).isoformat(),
                    "open": close_price - 0.45,
                    "high": close_price + 0.9,
                    "low": close_price - 1.1,
                    "close": close_price,
                    "volume": 1_000_000 + (index * 12_500),
                    "vwap": close_price - 0.2,
                }
            )

        result = market_context._summarize_underlying_price_history("AAPL", bars)

        self.assertTrue(result["available"])
        self.assertEqual(result["symbol"], "AAPL")
        self.assertEqual(result["source"], "alpaca_stock_bars")
        self.assertEqual(result["feed"], "iex")
        self.assertEqual(result["close_count"], 70)
        self.assertEqual(result["first_bar_timestamp"], bars[0]["timestamp"])
        self.assertEqual(result["last_bar_timestamp"], bars[-1]["timestamp"])
        self.assertIsNotNone(result["historical_volatility_20d"])
        self.assertIsNotNone(result["historical_volatility_60d"])
        self.assertGreater(result["historical_volatility_20d"], 0.0)
        self.assertGreater(result["historical_volatility_60d"], 0.0)
        self.assertEqual(len(result["recent_closes"]), 5)
        technicals = result["technical_indicators"]
        self.assertTrue(technicals["relative_volume"]["available"])
        self.assertTrue(technicals["price_vs_20ema"]["available"])
        self.assertTrue(technicals["price_vs_50sma"]["available"])
        self.assertTrue(technicals["rsi_14"]["available"])
        self.assertTrue(technicals["macd"]["available"])
        self.assertTrue(technicals["atr_overextension"]["available"])
        self.assertFalse(technicals["relative_strength"]["vs_spy"]["available"])
        technical_summary = result["technical_summary"]
        self.assertTrue(technical_summary["available"])
        self.assertEqual(technical_summary["trend_state"], "bullish")
        self.assertIn(technical_summary["call_entry_quality"], {"good", "excellent", "acceptable"})

    def test_enrich_with_alpaca_iv_percentiles_assigns_bucket_specific_contract_percentiles(self):
        first_summary = {
            "atm_mean_iv": 0.55,
            "term_structure": {
                "short_term_atm_iv": 0.60,
                "longer_term_atm_iv": 0.50,
            },
        }
        second_summary = {
            "atm_mean_iv": 0.55,
            "term_structure": {
                "short_term_atm_iv": 0.60,
                "longer_term_atm_iv": 0.50,
            },
        }
        first_contracts = [
            {
                "symbol": "AAPL_SHORT_DAY1",
                "expiration_date": "2026-01-05",
                "days_to_expiration": 3,
                "dte_bucket": "1_3",
                "strike_price": 101.0,
                "contract_type": "call",
                "implied_volatility": 0.40,
            },
            {
                "symbol": "AAPL_LONG_DAY1",
                "expiration_date": "2026-01-22",
                "days_to_expiration": 20,
                "dte_bucket": "15_30",
                "strike_price": 101.0,
                "contract_type": "call",
                "implied_volatility": 0.60,
            },
        ]
        second_contracts = [
            {
                "symbol": "AAPL_SHORT_DAY2",
                "expiration_date": "2026-01-06",
                "days_to_expiration": 3,
                "dte_bucket": "1_3",
                "strike_price": 101.0,
                "contract_type": "call",
                "implied_volatility": 0.80,
            },
            {
                "symbol": "AAPL_LONG_DAY2",
                "expiration_date": "2026-01-23",
                "days_to_expiration": 20,
                "dte_bucket": "15_30",
                "strike_price": 101.0,
                "contract_type": "call",
                "implied_volatility": 0.45,
            },
        ]

        class FirstSnapshotDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 1, 2, 14, 30, tzinfo=tz or timezone.utc)

        class SecondSnapshotDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 1, 3, 14, 30, tzinfo=tz or timezone.utc)

        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "option_iv_history.json"
            with iv_history_settings(iv_history_path=history_path, iv_bucket_min_history_samples=1):
                with patch.object(market_context, "datetime", FirstSnapshotDatetime):
                    market_context._enrich_with_alpaca_iv_percentiles(
                        "AAPL",
                        dict(first_summary),
                        contracts=first_contracts,
                        reference_stock_price=100.0,
                    )
                with patch.object(market_context, "datetime", SecondSnapshotDatetime):
                    second_result = market_context._enrich_with_alpaca_iv_percentiles(
                        "AAPL",
                        dict(second_summary),
                        contracts=second_contracts,
                        reference_stock_price=100.0,
                    )

        self.assertEqual(
            second_result["dte_bucket_iv_percentiles"]["1_3"],
            100.0,
        )
        self.assertEqual(
            second_result["dte_bucket_iv_percentiles"]["15_30"],
            0.0,
        )
        self.assertEqual(second_contracts[0]["iv_percentile"], 100.0)
        self.assertEqual(second_contracts[0]["iv_percentile_source"], "dte_bucket:1_3")
        self.assertEqual(second_contracts[1]["iv_percentile"], 0.0)
        self.assertEqual(second_contracts[1]["iv_percentile_source"], "dte_bucket:15_30")


if __name__ == "__main__":
    unittest.main()
