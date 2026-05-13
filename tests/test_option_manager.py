from __future__ import annotations

import importlib.util
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from services.position_manager import OptionPositionManagerService  # noqa: E402


FRONT_MAIN_SPEC = importlib.util.spec_from_file_location(
    "front_main_module_for_tests",
    PYTHON_SCRIPTS_DIR / "main.py",
)
assert FRONT_MAIN_SPEC is not None and FRONT_MAIN_SPEC.loader is not None
front_main = importlib.util.module_from_spec(FRONT_MAIN_SPEC)
FRONT_MAIN_SPEC.loader.exec_module(front_main)


class OptionPositionManagerServiceTests(unittest.TestCase):
    @patch("services.position_manager.JsonFileWriter.write")
    def test_run_cycle_executes_management_and_persists_output(self, mock_json_write):
        management_result = {
            "position_count": 3,
            "sell_count": 1,
            "close_submitted_count": 1,
            "positions": [{"symbol": "AAPL250117C00200000"}],
        }
        fake_manage_current_option_positions = MagicMock(return_value=management_result)
        fake_trading_module = SimpleNamespace(
            ManageCurrentOptionPositions=fake_manage_current_option_positions
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "option_manager_output.json"
            settings = SimpleNamespace(
                auto_close_option_positions=True,
                option_position_take_profit_pct=25.0,
                option_position_stop_loss_pct=-20.0,
                option_position_exit_hours_to_expiration=24.0,
            )
            paths = SimpleNamespace(
                option_position_management_output_path=output_path,
            )
            logger = MagicMock(spec=logging.Logger)
            service = OptionPositionManagerService(
                settings=settings,
                paths=paths,
                logger=logger,
            )

            trading_client = object()
            with patch.dict(sys.modules, {"Trading": fake_trading_module}):
                result = service.run_cycle(trading_client=trading_client)

        self.assertEqual(result, management_result)
        fake_manage_current_option_positions.assert_called_once_with(
            execute_sales=True,
            take_profit_pct=25.0,
            stop_loss_pct=-20.0,
            exit_hours_to_expiration=24.0,
            trading_client_override=trading_client,
        )
        mock_json_write.assert_called_once_with(output_path, management_result)
        logger.info.assert_called_once()
        log_message = logger.info.call_args.args[0]
        self.assertIn("Managed %s option positions", log_message)


class OptionManagerCompatibilityTests(unittest.TestCase):
    @patch.object(front_main.FRONT_MAIN_APP, "run_option_position_management_cycle")
    def test_main_wrapper_delegates_to_front_main_application(self, mock_run_cycle):
        expected = {
            "position_count": 2,
            "sell_count": 0,
            "close_submitted_count": 0,
        }
        mock_run_cycle.return_value = expected

        trading_client = object()
        result = front_main.run_option_position_management_cycle(
            trading_client=trading_client,
        )

        self.assertEqual(result, expected)
        mock_run_cycle.assert_called_once_with(trading_client=trading_client)


if __name__ == "__main__":
    unittest.main()
