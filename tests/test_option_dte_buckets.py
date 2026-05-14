from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
TRADING_SUPPORT_DIR = PYTHON_SCRIPTS_DIR / "trading_support"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from services.config import OptionPositionSettings  # noqa: E402
from services.option_dte_buckets import DteBucket  # noqa: E402


def _install_alpaca_stubs() -> None:
    if "alpaca" in sys.modules:
        return

    alpaca_module = types.ModuleType("alpaca")
    data_module = types.ModuleType("alpaca.data")
    data_requests_module = types.ModuleType("alpaca.data.requests")
    trading_module = types.ModuleType("alpaca.trading")
    trading_client_module = types.ModuleType("alpaca.trading.client")

    class _Request:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    class _TradingClient:
        pass

    data_requests_module.OptionLatestQuoteRequest = _Request
    data_requests_module.StockLatestQuoteRequest = _Request
    trading_client_module.TradingClient = _TradingClient

    sys.modules["alpaca"] = alpaca_module
    sys.modules["alpaca.data"] = data_module
    sys.modules["alpaca.data.requests"] = data_requests_module
    sys.modules["alpaca.trading"] = trading_module
    sys.modules["alpaca.trading.client"] = trading_client_module


def _load_option_positions_module():
    _install_alpaca_stubs()

    package_name = "_test_trading_support_buckets"
    if package_name not in sys.modules:
        package_module = types.ModuleType(package_name)
        package_module.__path__ = [str(TRADING_SUPPORT_DIR)]
        sys.modules[package_name] = package_module

    utils_module_name = f"{package_name}.utils"
    if utils_module_name not in sys.modules:
        utils_spec = importlib.util.spec_from_file_location(
            utils_module_name,
            TRADING_SUPPORT_DIR / "utils.py",
        )
        assert utils_spec is not None and utils_spec.loader is not None
        utils_module = importlib.util.module_from_spec(utils_spec)
        sys.modules[utils_module_name] = utils_module
        utils_spec.loader.exec_module(utils_module)

    clients_module_name = f"{package_name}.clients"
    clients_module = types.ModuleType(clients_module_name)
    clients_module.get_default_trading_client = lambda: None
    clients_module.get_option_history_client = lambda: types.SimpleNamespace(
        get_option_latest_quote=lambda request: {}
    )
    clients_module.get_stock_history_client = lambda: types.SimpleNamespace(
        get_stock_latest_quote=lambda request: {}
    )
    sys.modules[clients_module_name] = clients_module

    module_name = f"{package_name}.option_positions"
    module_spec = importlib.util.spec_from_file_location(
        module_name,
        TRADING_SUPPORT_DIR / "option_positions.py",
    )
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    module_spec.loader.exec_module(module)
    return module


class OptionDteBucketRegressionTests(unittest.TestCase):
    def test_option_position_settings_preserve_none_force_exit_override(self) -> None:
        custom_bucket = DteBucket(
            key="30_45",
            label="30-45 DTE",
            min_days=30,
            max_days=45,
            force_exit_days_to_expiration=None,
            default_take_profit_pct=80.0,
            default_stop_loss_pct=-45.0,
        )

        with patch("services.config.OPTION_DTE_BUCKETS", (custom_bucket,)):
            settings = OptionPositionSettings.from_env()

        self.assertEqual(len(settings.dte_rules), 1)
        self.assertIsNone(settings.dte_rules[0].force_exit_days_to_expiration)

    def test_bucket_with_no_force_exit_override_uses_default_exit_hours(self) -> None:
        option_positions = _load_option_positions_module()
        original_rules = option_positions.OPTION_EXIT_DTE_RULES
        option_positions.OPTION_EXIT_DTE_RULES = (
            option_positions.OptionExitRule(
                label="30-45 DTE",
                min_days_to_expiration=30,
                max_days_to_expiration=45,
                take_profit_pct=80.0,
                stop_loss_pct=-45.0,
                force_exit_days_to_expiration=None,
            ),
        )
        try:
            with patch.object(
                option_positions,
                "resolve_dte_bucket_for_days",
                return_value=types.SimpleNamespace(label="30-45 DTE"),
            ):
                thresholds = option_positions._resolve_option_exit_thresholds(
                    days_to_expiration=35,
                    default_take_profit_pct=25.0,
                    default_stop_loss_pct=-20.0,
                    default_exit_hours_to_expiration=24.0,
                )
        finally:
            option_positions.OPTION_EXIT_DTE_RULES = original_rules

        self.assertEqual(thresholds.dte_rule_label, "30-45 DTE")
        self.assertEqual(thresholds.take_profit_pct, 80.0)
        self.assertEqual(thresholds.stop_loss_pct, -45.0)
        self.assertIsNone(thresholds.force_exit_days_to_expiration)
        self.assertEqual(thresholds.exit_hours_to_expiration, 24.0)
        self.assertFalse(thresholds.is_default_rule)


if __name__ == "__main__":
    unittest.main()
