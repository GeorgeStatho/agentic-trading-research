"""Settings that several readers share must resolve the same way everywhere."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from portfolio_history_service import build_portfolio_history_url
from services.core.trading_config import AlpacaSettings, ApiPaths, ApiSettings, WorkerPaths, WorkerSettings
from services.thesis.thesis_validator_service import ThesisValidatorSettings


class AlpacaPaperFlagTests(unittest.TestCase):
    def test_any_value_except_an_explicit_off_keeps_paper_trading(self) -> None:
        for value in ("true", "on", "y", "TRUE ", "1"):
            with self.subTest(value=value), patch.dict("os.environ", {"ALPACA_PAPER": value}):
                self.assertTrue(AlpacaSettings.from_env().paper)
                self.assertTrue(build_portfolio_history_url().startswith("https://paper-api."))

    def test_explicit_off_selects_live(self) -> None:
        for value in ("false", "0", "no", "off"):
            with self.subTest(value=value), patch.dict("os.environ", {"ALPACA_PAPER": value}):
                self.assertFalse(AlpacaSettings.from_env().paper)


class ApiMirrorsWorkerTests(unittest.TestCase):
    def test_api_limits_and_paths_come_from_the_worker_settings(self) -> None:
        env = {"MAX_CONCURRENT_OPTION_POSITIONS": "7", "AGENT_OPTION_ORDER_QTY": "not-a-number",
               "TRADE_EXECUTION_OUTPUT_PATH": "/tmp/trades.json"}
        with patch.dict("os.environ", env):
            worker, api = WorkerSettings.from_env(), ApiSettings.from_env()
            worker_paths, api_paths = WorkerPaths.from_env(), ApiPaths.from_env()
        self.assertEqual((api.max_concurrent_option_positions, api.default_option_order_qty),
                         (worker.max_concurrent_option_positions, worker.default_option_order_qty))
        self.assertEqual(api.default_option_order_qty, 1)
        self.assertEqual(api_paths.trade_execution_output_path, worker_paths.trade_output_path)


class ThesisValidatorKillSwitchTests(unittest.TestCase):
    def test_kill_switch_uses_the_shared_flag_rule(self) -> None:
        with patch.dict("os.environ", {"THESIS_VALIDATOR_KILL_SWITCH": "enabled"}):
            self.assertTrue(ThesisValidatorSettings.from_env().kill_switch)
        with patch.dict("os.environ", {"THESIS_VALIDATOR_KILL_SWITCH": "off"}):
            self.assertFalse(ThesisValidatorSettings.from_env().kill_switch)


if __name__ == "__main__":
    unittest.main()
