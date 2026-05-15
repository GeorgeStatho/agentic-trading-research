from __future__ import annotations

import importlib
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)


def _install_alpaca_stubs() -> None:
    if "alpaca" in sys.modules:
        return

    alpaca_module = types.ModuleType("alpaca")
    data_module = types.ModuleType("alpaca.data")
    data_requests_module = types.ModuleType("alpaca.data.requests")
    trading_module = types.ModuleType("alpaca.trading")
    trading_client_module = types.ModuleType("alpaca.trading.client")
    trading_enums_module = types.ModuleType("alpaca.trading.enums")
    trading_requests_module = types.ModuleType("alpaca.trading.requests")

    class _OptionHistoricalDataClient:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    class _StockHistoricalDataClient:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    class _TradingClient:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    class _Request:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    class _ContractType:
        CALL = "call"

    class _OrderSide:
        BUY = "buy"
        SELL = "sell"

    class _TimeInForce:
        DAY = "day"
        FOK = "fok"
        GTC = "gtc"
        IOC = "ioc"

    data_module.OptionHistoricalDataClient = _OptionHistoricalDataClient
    data_module.StockHistoricalDataClient = _StockHistoricalDataClient
    data_requests_module.OptionLatestQuoteRequest = _Request
    data_requests_module.StockLatestQuoteRequest = _Request
    trading_client_module.TradingClient = _TradingClient
    trading_enums_module.ContractType = _ContractType
    trading_enums_module.OrderSide = _OrderSide
    trading_enums_module.TimeInForce = _TimeInForce
    trading_requests_module.GetOptionContractsRequest = _Request
    trading_requests_module.LimitOrderRequest = _Request
    trading_requests_module.MarketOrderRequest = _Request

    sys.modules["alpaca"] = alpaca_module
    sys.modules["alpaca.data"] = data_module
    sys.modules["alpaca.data.requests"] = data_requests_module
    sys.modules["alpaca.trading"] = trading_module
    sys.modules["alpaca.trading.client"] = trading_client_module
    sys.modules["alpaca.trading.enums"] = trading_enums_module
    sys.modules["alpaca.trading.requests"] = trading_requests_module


class TradingCompatibilityTests(unittest.TestCase):
    def test_trading_module_keeps_legacy_api_key_exports(self) -> None:
        _install_alpaca_stubs()
        target_modules = [
            "Trading",
            "trading_support",
            "trading_support.__init__",
            "trading_support.clients",
        ]
        preserved_modules = {
            name: sys.modules.get(name)
            for name in target_modules
        }
        for name in target_modules:
            sys.modules.pop(name, None)

        try:
            with patch.dict(
                os.environ,
                {
                    "PUBLIC_KEY": "test-public-key",
                    "PRIVATE_KEY": "test-private-secret",
                    "ALPACA_PAPER": "true",
                },
                clear=False,
            ):
                trading_module = importlib.import_module("Trading")
                self.assertEqual(trading_module.API_KEY, "test-public-key")
                self.assertEqual(trading_module.API_SECRET_KEY, "test-private-secret")
                self.assertTrue(hasattr(trading_module, "ManageCurrentOptionPositions"))
        finally:
            for name in target_modules:
                sys.modules.pop(name, None)
            for name, module in preserved_modules.items():
                if module is not None:
                    sys.modules[name] = module


if __name__ == "__main__":
    unittest.main()
