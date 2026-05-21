from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

import agent_helpers.market_context as market_context  # noqa: E402
from agent_stages.manager import _build_manager_visible_market_context  # noqa: E402


class MarketContextVolatilityTests(unittest.TestCase):
    def test_summarize_underlying_price_history_computes_realized_volatility_from_alpaca_bars(self):
        start_time = datetime(2026, 1, 2, tzinfo=timezone.utc)
        bars = []
        for index in range(70):
            bars.append(
                {
                    "timestamp": (start_time + timedelta(days=index)).isoformat(),
                    "close": 100.0 + (index * 0.35) + ((index % 4) - 1.5) * 0.8,
                }
            )

        result = market_context._summarize_underlying_price_history("AAPL", bars)

        self.assertTrue(result["available"])
        self.assertEqual(result["symbol"], "AAPL")
        self.assertEqual(result["source"], "alpaca_stock_bars")
        self.assertEqual(result["close_count"], 70)
        self.assertEqual(result["first_bar_timestamp"], bars[0]["timestamp"])
        self.assertEqual(result["last_bar_timestamp"], bars[-1]["timestamp"])
        self.assertIsNotNone(result["historical_volatility_20d"])
        self.assertIsNotNone(result["historical_volatility_60d"])
        self.assertGreater(result["historical_volatility_20d"], 0.0)
        self.assertGreater(result["historical_volatility_60d"], 0.0)
        self.assertEqual(len(result["recent_closes"]), 5)

    def test_summarize_underlying_price_history_handles_empty_bar_set(self):
        result = market_context._summarize_underlying_price_history("AAPL", [])

        self.assertFalse(result["available"])
        self.assertEqual(result["source"], "alpaca_stock_bars")
        self.assertEqual(result["close_count"], 0)
        self.assertIn("No valid close prices", result["error"])

    def test_build_manager_visible_market_context_preserves_volatility_context(self):
        payload = {
            "market_context": {
                "current_stock_price": {"available": True, "price": 201.5},
                "underlying_price_history": {
                    "available": True,
                    "source": "alpaca_stock_bars",
                    "historical_volatility_20d": 0.31,
                    "historical_volatility_60d": 0.28,
                },
                "market_indices": {},
                "sector_etf": {},
                "account_state": {},
                "option_market": {
                    "available": True,
                    "underlying_symbol": "AAPL",
                    "contract_count": 8,
                    "available_expirations": [],
                    "available_strikes": [],
                    "volatility_summary": {
                        "atm_mean_iv": 0.62,
                        "atm_iv_vs_hv": {
                            "available": True,
                            "iv_hv_ratio": 2.0,
                            "pricing_band": "expensive",
                        },
                    },
                },
            }
        }

        result = _build_manager_visible_market_context(payload)

        self.assertEqual(result["underlying_price_history"]["source"], "alpaca_stock_bars")
        self.assertEqual(
            result["option_market_summary"]["volatility_summary"]["atm_iv_vs_hv"]["pricing_band"],
            "expensive",
        )

    def test_enrich_with_alpaca_iv_percentiles_persists_history_and_computes_percentile(self):
        first_summary = {
            "atm_mean_iv": 0.40,
            "term_structure": {
                "short_term_atm_iv": 0.45,
                "longer_term_atm_iv": 0.35,
            },
        }
        second_summary = {
            "atm_mean_iv": 0.60,
            "term_structure": {
                "short_term_atm_iv": 0.70,
                "longer_term_atm_iv": 0.45,
            },
        }

        class FirstSnapshotDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 1, 2, 14, 30, tzinfo=tz or timezone.utc)

        class SecondSnapshotDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 1, 2, 15, 45, tzinfo=tz or timezone.utc)

        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "option_iv_history.json"
            with patch.object(market_context, "OPTION_IV_HISTORY_PATH", history_path):
                with patch.object(market_context, "datetime", FirstSnapshotDatetime):
                    first_result = market_context._enrich_with_alpaca_iv_percentiles("AAPL", dict(first_summary))
                with patch.object(market_context, "datetime", SecondSnapshotDatetime):
                    second_result = market_context._enrich_with_alpaca_iv_percentiles("AAPL", dict(second_summary))

        self.assertEqual(first_result["iv_percentile_source"], "alpaca_snapshot_history")
        self.assertIsNone(first_result["atm_iv_percentile"])
        self.assertEqual(second_result["atm_iv_percentile"], 100.0)
        self.assertEqual(
            second_result["term_structure"]["short_term_iv_percentile"],
            100.0,
        )
        self.assertEqual(second_result["iv_history_sample_count"], 2)

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
            with patch.object(market_context, "OPTION_IV_HISTORY_PATH", history_path):
                with patch.object(market_context, "OPTION_IV_BUCKET_MIN_HISTORY_SAMPLES", 1):
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
