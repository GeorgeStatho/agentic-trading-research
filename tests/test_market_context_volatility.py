from __future__ import annotations

import os
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

from _paths import load_project_env  # noqa: E402

load_project_env()

import agent_helpers.market_context as market_context  # noqa: E402
from agent_helpers.market_context import underlying as underlying_module  # noqa: E402
from agent_helpers.market_context import underlying_bars as underlying_bars_module  # noqa: E402
from agent_helpers.market_context import common as market_context_common  # noqa: E402
from agent_stages.manager_payload_context import _build_manager_visible_market_context  # noqa: E402


class MarketContextVolatilityTests(unittest.TestCase):
    def test_daily_stock_bar_request_explicitly_uses_iex(self):
        class FakeStockClient:
            request = None

            def get_stock_bars(self, request):
                self.request = request
                return {"AAPL": []}

        class FakeStockBarsRequest:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class FakeTimeFrame:
            Day = "1Day"

        class FakeDataFeed:
            IEX = "iex"

        stock_client = FakeStockClient()
        with (
            patch.object(underlying_bars_module, "_get_alpaca_clients", return_value={"stock": stock_client}),
            patch.object(underlying_bars_module, "StockBarsRequest", FakeStockBarsRequest),
            patch.object(underlying_bars_module, "TimeFrame", FakeTimeFrame),
            patch.object(underlying_bars_module, "DataFeed", FakeDataFeed),
        ):
            bars, error = underlying_bars_module._fetch_daily_stock_bars("aapl")

        self.assertEqual(bars, [])
        self.assertEqual(error, "")
        self.assertEqual(stock_client.request.kwargs["feed"], FakeDataFeed.IEX)

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
                    "technical_indicators": {
                        "rsi_14": {"available": True, "value": 63.2},
                    },
                    "technical_summary": {
                        "available": True,
                        "call_entry_quality": "acceptable",
                    },
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
        self.assertEqual(result["underlying_price_history"]["technical_indicators"]["rsi_14"]["value"], 63.2)
        self.assertEqual(result["underlying_price_history"]["technical_summary"]["call_entry_quality"], "acceptable")
        self.assertEqual(
            result["option_market_summary"]["volatility_summary"]["atm_iv_vs_hv"]["pricing_band"],
            "expensive",
        )

    def test_build_underlying_price_history_snapshot_computes_relative_strength_against_spy_and_sector(self):
        start_time = datetime(2026, 1, 2, tzinfo=timezone.utc)

        def make_bars(base_price: float, slope: float, volume_base: float) -> list[dict[str, float | str]]:
            bars: list[dict[str, float | str]] = []
            for index in range(90):
                close_price = base_price + (index * slope) + ((index % 5) - 2) * 0.3
                bars.append(
                    {
                        "timestamp": (start_time + timedelta(days=index)).isoformat(),
                        "open": close_price - 0.6,
                        "high": close_price + 1.1,
                        "low": close_price - 1.2,
                        "close": close_price,
                        "volume": volume_base + (index * 1000),
                        "vwap": close_price - 0.15,
                    }
                )
            return bars

        bars_by_symbol = {
            "AAPL": make_bars(100.0, 0.65, 1_000_000),
            "SPY": make_bars(500.0, 0.20, 5_000_000),
            "XLK": make_bars(200.0, 0.30, 2_000_000),
        }

        def fake_fetch(symbol: str) -> tuple[list[dict[str, float | str]], str]:
            return list(bars_by_symbol.get(symbol, [])), ""

        with patch.object(underlying_module, "_fetch_daily_stock_bars", side_effect=fake_fetch):
            result = underlying_module._build_underlying_price_history_snapshot(
                "AAPL",
                reference_price=161.25,
                sector_benchmark_symbol="XLK",
            )

        technicals = result["technical_indicators"]
        self.assertTrue(result["available"])
        self.assertEqual(technicals["reference_price"], 161.25)
        self.assertTrue(technicals["vwap"]["available"])
        self.assertTrue(technicals["relative_strength"]["vs_spy"]["available"])
        self.assertEqual(technicals["relative_strength"]["vs_spy"]["benchmark_symbol"], "SPY")
        self.assertIsNotNone(technicals["relative_strength"]["vs_spy"]["returns"]["20d"]["relative_return_pct"])
        self.assertTrue(technicals["relative_strength"]["vs_sector_etf"]["available"])
        self.assertEqual(technicals["relative_strength"]["vs_sector_etf"]["benchmark_symbol"], "XLK")
        self.assertIsNotNone(technicals["relative_strength"]["vs_sector_etf"]["price_ratio"])
        self.assertTrue(technicals["sector_market_relative_strength"]["available"])
        self.assertEqual(technicals["sector_market_relative_strength"]["sector_etf"], "XLK")
        self.assertEqual(technicals["sector_market_relative_strength"]["benchmark"], "SPY")
        self.assertIn(technicals["sector_market_relative_strength"]["state"], {"outperforming", "underperforming", "flat"})
        technical_summary = result["technical_summary"]
        self.assertTrue(technical_summary["available"])
        self.assertEqual(technical_summary["trend_state"], "bullish")
        self.assertEqual(technical_summary["relative_strength_confirmation"], "strong")
        self.assertIn("VWAP is daily-bar VWAP, not true intraday VWAP", technical_summary["warnings"])

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


@unittest.skipUnless(
    os.getenv("PUBLIC_KEY") and os.getenv("PRIVATE_KEY"),
    "Alpaca integration test requires PUBLIC_KEY and PRIVATE_KEY",
)
class MarketContextLiveIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if market_context_common.ALPACA_IMPORT_ERROR is not None:
            raise unittest.SkipTest(
                f"alpaca-py is unavailable: {market_context_common.ALPACA_IMPORT_ERROR}"
            )
        market_context_common._ALPACA_CLIENTS = None

    def test_live_market_context_builds_when_alpaca_credentials_are_available(self):
        market_payload = market_context.build_market_context(
            {
                "symbol": "AAPL",
                "sector_key": "technology",
                "sector_name": "Technology",
            },
            option_expiration_date=None,
            option_expiration_date_gte=None,
            option_expiration_date_lte=None,
            option_strike_price_gte=None,
            option_strike_price_lte=None,
            option_contract_limit_per_type=2,
        )

        self.assertIn("current_stock_price", market_payload)
        self.assertIn("underlying_price_history", market_payload)
        self.assertIn("market_indices", market_payload)
        self.assertIn("sector_etf", market_payload)
        self.assertIn("option_market", market_payload)
        self.assertIn("account_state", market_payload)

        stock_snapshot = market_payload["current_stock_price"]
        underlying_snapshot = market_payload["underlying_price_history"]
        technical_summary = underlying_snapshot.get("technical_summary", {})
        sector_etf = market_payload["sector_etf"]
        option_market = market_payload["option_market"]

        self.assertIsInstance(stock_snapshot, dict)
        self.assertIsInstance(underlying_snapshot, dict)
        self.assertIsInstance(sector_etf, dict)
        self.assertIsInstance(option_market, dict)
        self.assertEqual(str(stock_snapshot.get("symbol") or "").upper(), "AAPL")
        self.assertEqual(str(underlying_snapshot.get("symbol") or "").upper(), "AAPL")

        if stock_snapshot.get("available"):
            self.assertIsNotNone(stock_snapshot.get("price"))
        else:
            self.assertTrue(str(stock_snapshot.get("error") or "").strip())

        if underlying_snapshot.get("available"):
            self.assertGreater(int(underlying_snapshot.get("close_count") or 0), 0)
            self.assertIn("technical_indicators", underlying_snapshot)
            self.assertIn("technical_summary", underlying_snapshot)
            self.assertIsInstance(technical_summary, dict)
            if technical_summary.get("available"):
                self.assertIn(
                    technical_summary.get("technical_confirmation_for_calls"),
                    {"strong", "moderate", "mixed", "weak", "bearish", "unknown"},
                )
                self.assertIn(
                    technical_summary.get("recommended_llm_adjustment"),
                    {
                        "approve_if_news_and_options_confirm",
                        "reduce_confidence",
                        "reduce_size",
                        "wait_for_better_entry",
                        "skip_unless_exceptional_catalyst",
                    },
                )
        else:
            self.assertTrue(str(underlying_snapshot.get("error") or "").strip())

        if sector_etf.get("available"):
            self.assertTrue(str(sector_etf.get("ticker") or "").strip())
        else:
            self.assertTrue(str(sector_etf.get("error") or "").strip())

        if option_market.get("available"):
            self.assertIn("volatility_summary", option_market)
        else:
            self.assertTrue(str(option_market.get("error") or "").strip())


if __name__ == "__main__":
    unittest.main()
