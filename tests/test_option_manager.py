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

from services.option_dte_buckets import OPTION_DTE_BUCKETS  # noqa: E402
from services.position_manager import OptionPositionManagerService  # noqa: E402


FRONT_MAIN_SPEC = importlib.util.spec_from_file_location(
    "front_main_module_for_tests",
    PYTHON_SCRIPTS_DIR / "main.py",
)
assert FRONT_MAIN_SPEC is not None and FRONT_MAIN_SPEC.loader is not None
front_main = importlib.util.module_from_spec(FRONT_MAIN_SPEC)
FRONT_MAIN_SPEC.loader.exec_module(front_main)


class VerboseTestCase(unittest.TestCase):
    def log_pass(self, message: str) -> None:
        print(f"[PASS] {self.__class__.__name__}.{self._testMethodName}: {message}")


def _build_bucket_giveback_fixture() -> dict[str, float]:
    base_value = 0.30
    step = 0.05
    return {
        bucket.key: round(base_value + (index * step), 2)
        for index, bucket in enumerate(OPTION_DTE_BUCKETS, start=1)
    }


def _build_position_manager_settings(bucket_giveback_fixture: dict[str, float], **overrides) -> SimpleNamespace:
    base = {
        "auto_close_option_positions": True,
        "option_position_take_profit_pct": 25.0,
        "option_position_stop_loss_pct": -20.0,
        "option_position_exit_hours_to_expiration": 24.0,
        "option_position_enable_trailing_profit": True,
        "option_position_trailing_profit_dry_run": False,
        "option_position_enable_momentum_exit": True,
        "option_momentum_history_enable_after_pnl_pct": 2.00,
        "option_momentum_history_window_size": 10,
        "option_momentum_history_min_samples": 5,
        "option_momentum_history_bad_count_exit_threshold": 6,
        "option_momentum_history_consecutive_bad_exit_threshold": 3,
        "option_price_momentum_bad_giveback_threshold": 0.40,
        "option_price_momentum_mixed_giveback_threshold": 0.20,
        "option_price_momentum_good_profit_threshold": 0.12,
        "option_trail_protection_trigger_pct": 0.40,
        "option_trail_initial_floor_pct": 0.10,
        "option_trail_first_scale_out_trigger_pct": 0.55,
        "option_trail_first_scale_out_fraction": 0.50,
        "option_trail_second_scale_out_trigger_pct": 2.00,
        "option_trail_second_scale_out_fraction": 0.25,
        "option_trail_giveback_pct_by_bucket_key": bucket_giveback_fixture,
        "option_trail_100_floor_pct": 0.60,
        "option_trail_150_floor_pct": 1.00,
        "option_trail_200_floor_pct": 1.40,
        "option_pending_exit_stale_minutes": 10.0,
        "option_pending_exit_cancel_on_stale": True,
        "manager_decision_pnl_retention_days": 14,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class OptionPositionManagerServiceTests(VerboseTestCase):
    @patch("services.position_manager.update_manager_decision_latest_pnl")
    @patch("services.position_manager.get_latest_linked_manager_decision_for_option_symbol")
    @patch("services.position_manager.expire_manager_decision_pnl")
    @patch("services.position_manager.JsonFileWriter.write")
    def test_run_cycle_executes_management_and_persists_output(
        self,
        mock_json_write,
        mock_expire_manager_decision_pnl,
        mock_get_latest_linked_manager_decision,
        mock_update_manager_decision_latest_pnl,
    ):
        bucket_giveback_fixture = _build_bucket_giveback_fixture()
        management_result = {
            "position_count": 3,
            "sell_count": 1,
            "close_submitted_count": 1,
            "momentum_history_config_notes": [],
            "positions": [{"symbol": "AAPL250117C00200000"}],
        }
        fake_manage_current_option_positions = MagicMock(return_value=management_result)
        fake_trading_module = SimpleNamespace(
            ManageCurrentOptionPositions=fake_manage_current_option_positions
        )
        mock_expire_manager_decision_pnl.return_value = 0
        mock_get_latest_linked_manager_decision.return_value = None

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "option_manager_output.json"
            state_path = Path(temp_dir) / "option_state.json"
            settings = _build_position_manager_settings(bucket_giveback_fixture)
            paths = SimpleNamespace(
                option_position_management_output_path=output_path,
                option_position_state_path=state_path,
            )
            trading_gateway = MagicMock()
            logger = MagicMock(spec=logging.Logger)
            service = OptionPositionManagerService(
                settings=settings,
                paths=paths,
                trading_gateway=trading_gateway,
                logger=logger,
            )

            trading_client = object()
            with patch.dict(sys.modules, {"Trading": fake_trading_module}):
                result = service.run_cycle(trading_client=trading_client)

        self.assertEqual(result, management_result)
        mock_expire_manager_decision_pnl.assert_called_once()
        mock_get_latest_linked_manager_decision.assert_called_once_with(
            selected_option_symbol="AAPL250117C00200000",
        )
        mock_update_manager_decision_latest_pnl.assert_not_called()
        fake_manage_current_option_positions.assert_called_once()
        call_kwargs = fake_manage_current_option_positions.call_args.kwargs
        self.assertEqual(call_kwargs["execute_sales"], True)
        self.assertEqual(call_kwargs["take_profit_pct"], 25.0)
        self.assertEqual(call_kwargs["stop_loss_pct"], -20.0)
        self.assertEqual(call_kwargs["exit_hours_to_expiration"], 24.0)
        self.assertEqual(call_kwargs["state_path_override"], state_path)
        self.assertEqual(call_kwargs["enable_trailing_profit_override"], True)
        self.assertEqual(call_kwargs["trailing_profit_dry_run_override"], False)
        self.assertEqual(call_kwargs["enable_momentum_exit_override"], True)
        self.assertEqual(call_kwargs["momentum_history_enable_after_pnl_pct_override"], 2.00)
        self.assertEqual(call_kwargs["momentum_history_window_size_override"], 10)
        self.assertEqual(call_kwargs["momentum_history_min_samples_override"], 5)
        self.assertEqual(call_kwargs["momentum_history_bad_count_exit_threshold_override"], 6)
        self.assertEqual(call_kwargs["momentum_history_consecutive_bad_exit_threshold_override"], 3)
        self.assertEqual(call_kwargs["option_price_momentum_bad_giveback_threshold_override"], 0.40)
        self.assertEqual(call_kwargs["option_price_momentum_mixed_giveback_threshold_override"], 0.20)
        self.assertEqual(call_kwargs["option_price_momentum_good_profit_threshold_override"], 0.12)
        self.assertEqual(call_kwargs["trail_protection_trigger_pct_override"], 0.40)
        self.assertEqual(call_kwargs["trail_initial_floor_pct_override"], 0.10)
        self.assertEqual(
            call_kwargs["trail_giveback_pct_by_bucket_key_override"],
            bucket_giveback_fixture,
        )
        self.assertEqual(call_kwargs["pending_exit_stale_minutes_override"], 10.0)
        self.assertEqual(call_kwargs["pending_exit_cancel_on_stale_override"], True)
        self.assertIs(call_kwargs["trading_client_override"], trading_client)
        self.assertTrue(callable(call_kwargs["submit_full_exit_order"]))
        self.assertTrue(callable(call_kwargs["submit_partial_exit_order"]))
        self.assertTrue(callable(call_kwargs["resolve_order_status"]))
        self.assertTrue(callable(call_kwargs["cancel_order"]))
        mock_json_write.assert_called_once_with(output_path, management_result)
        logger.info.assert_called_once()
        log_message = logger.info.call_args.args[0]
        self.assertIn("Managed %s option positions", log_message)
        self.log_pass("option manager service forwarded trailing-profit settings, callbacks, and persisted the cycle output")

    @patch("services.position_manager.update_manager_decision_latest_pnl")
    @patch("services.position_manager.get_latest_linked_manager_decision_for_option_symbol")
    @patch("services.position_manager.expire_manager_decision_pnl")
    @patch("services.position_manager.JsonFileWriter.write")
    def test_run_cycle_refreshes_manager_history_pnl_for_open_position(
        self,
        mock_json_write,
        mock_expire_manager_decision_pnl,
        mock_get_latest_linked_manager_decision,
        mock_update_manager_decision_latest_pnl,
    ):
        bucket_giveback_fixture = _build_bucket_giveback_fixture()
        management_result = {
            "position_count": 1,
            "sell_count": 0,
            "close_submitted_count": 0,
            "momentum_history_config_notes": [],
            "positions": [
                {
                    "symbol": "MSFT260619C00450000",
                    "broker_unrealized_pl_pct": 12.5,
                    "last_action_at": "2026-05-22T14:05:00+00:00",
                    "close_submitted": False,
                }
            ],
        }
        fake_manage_current_option_positions = MagicMock(return_value=management_result)
        fake_trading_module = SimpleNamespace(
            ManageCurrentOptionPositions=fake_manage_current_option_positions
        )
        mock_expire_manager_decision_pnl.return_value = 0
        mock_get_latest_linked_manager_decision.return_value = {"id": 91}
        mock_update_manager_decision_latest_pnl.return_value = True

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "option_manager_output.json"
            state_path = Path(temp_dir) / "option_state.json"
            settings = _build_position_manager_settings(
                bucket_giveback_fixture,
                manager_decision_pnl_retention_days=21,
            )
            paths = SimpleNamespace(
                option_position_management_output_path=output_path,
                option_position_state_path=state_path,
            )
            service = OptionPositionManagerService(
                settings=settings,
                paths=paths,
                trading_gateway=MagicMock(),
                logger=MagicMock(spec=logging.Logger),
            )

            with patch.dict(sys.modules, {"Trading": fake_trading_module}):
                service.run_cycle(trading_client=object())

        mock_json_write.assert_called_once_with(output_path, management_result)
        mock_expire_manager_decision_pnl.assert_called_once()
        mock_get_latest_linked_manager_decision.assert_called_once_with(
            selected_option_symbol="MSFT260619C00450000",
        )
        mock_update_manager_decision_latest_pnl.assert_called_once()
        update_kwargs = mock_update_manager_decision_latest_pnl.call_args.kwargs
        self.assertEqual(update_kwargs["decision_history_id"], 91)
        self.assertEqual(update_kwargs["latest_trade_pnl_pct"], 12.5)
        self.assertEqual(update_kwargs["latest_trade_pnl_updated_at"], "2026-05-22T14:05:00+00:00")
        self.assertEqual(update_kwargs["resolved_outcome_label"], "open_profit")
        self.assertIn("T", update_kwargs["pnl_expires_at"])
        self.log_pass("service refreshes manager decision history with current open-position P/L snapshots")

    @patch("services.position_manager.update_manager_decision_latest_pnl")
    @patch("services.position_manager.get_latest_linked_manager_decision_for_option_symbol")
    @patch("services.position_manager.expire_manager_decision_pnl")
    @patch("services.position_manager.JsonFileWriter.write")
    def test_run_cycle_records_terminal_snapshot_when_close_is_submitted(
        self,
        mock_json_write,
        mock_expire_manager_decision_pnl,
        mock_get_latest_linked_manager_decision,
        mock_update_manager_decision_latest_pnl,
    ):
        bucket_giveback_fixture = _build_bucket_giveback_fixture()
        management_result = {
            "position_count": 1,
            "sell_count": 1,
            "close_submitted_count": 1,
            "momentum_history_config_notes": [],
            "positions": [
                {
                    "symbol": "MSFT260619C00450000",
                    "underlying_symbol": "MSFT",
                    "broker_unrealized_pl_pct": -4.25,
                    "last_action_at": "2026-05-22T15:00:00+00:00",
                    "close_submitted": True,
                    "close_order_id": "close-123",
                    "close_order_status": "accepted",
                    "close_requested_qty": 1,
                    "expiration_date": "2026-06-19",
                    "strike": 450.0,
                }
            ],
        }
        fake_manage_current_option_positions = MagicMock(return_value=management_result)
        fake_trading_module = SimpleNamespace(
            ManageCurrentOptionPositions=fake_manage_current_option_positions
        )
        mock_expire_manager_decision_pnl.return_value = 0
        mock_get_latest_linked_manager_decision.return_value = {"id": 44}
        mock_update_manager_decision_latest_pnl.return_value = True
        trade_journal = MagicMock()
        trade_journal.record_position_exit.return_value = 7

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "option_manager_output.json"
            state_path = Path(temp_dir) / "option_state.json"
            settings = _build_position_manager_settings(bucket_giveback_fixture)
            paths = SimpleNamespace(
                option_position_management_output_path=output_path,
                option_position_state_path=state_path,
            )
            service = OptionPositionManagerService(
                settings=settings,
                paths=paths,
                trading_gateway=MagicMock(),
                trade_journal=trade_journal,
                logger=MagicMock(spec=logging.Logger),
            )

            with patch.dict(sys.modules, {"Trading": fake_trading_module}):
                result = service.run_cycle(trading_client=object())

        mock_json_write.assert_called_once_with(output_path, management_result)
        trade_journal.record_position_exit.assert_called_once()
        self.assertTrue(result["positions"][0]["db_recorded"])
        self.assertEqual(result["positions"][0]["db_record_id"], 7)
        mock_update_manager_decision_latest_pnl.assert_called_once()
        update_kwargs = mock_update_manager_decision_latest_pnl.call_args.kwargs
        self.assertEqual(update_kwargs["decision_history_id"], 44)
        self.assertEqual(update_kwargs["latest_trade_pnl_pct"], -4.25)
        self.assertEqual(update_kwargs["resolved_outcome_label"], "managed_exit_submitted_loss")
        self.log_pass("service stores a terminal managed-exit snapshot before the closed position falls out of later refresh cycles")

    @patch("services.position_manager.update_manager_decision_latest_pnl")
    @patch("services.position_manager.get_latest_linked_manager_decision_for_option_symbol")
    @patch("services.position_manager.expire_manager_decision_pnl")
    @patch("services.position_manager.JsonFileWriter.write")
    def test_run_cycle_normalizes_impossible_momentum_history_settings(
        self,
        mock_json_write,
        mock_expire_manager_decision_pnl,
        mock_get_latest_linked_manager_decision,
        mock_update_manager_decision_latest_pnl,
    ):
        bucket_giveback_fixture = _build_bucket_giveback_fixture()
        fake_manage_current_option_positions = MagicMock(
            return_value={
                "position_count": 0,
                "sell_count": 0,
                "close_submitted_count": 0,
                "momentum_history_config_notes": [
                    "Momentum history min_samples was normalized from 12 to 10 to fit within the window size."
                ],
                "positions": [],
            }
        )
        fake_trading_module = SimpleNamespace(
            ManageCurrentOptionPositions=fake_manage_current_option_positions
        )
        mock_expire_manager_decision_pnl.return_value = 0

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "option_manager_output.json"
            state_path = Path(temp_dir) / "option_state.json"
            settings = _build_position_manager_settings(
                bucket_giveback_fixture,
                option_momentum_history_min_samples=12,
                option_momentum_history_bad_count_exit_threshold=11,
                option_momentum_history_consecutive_bad_exit_threshold=15,
            )
            paths = SimpleNamespace(
                option_position_management_output_path=output_path,
                option_position_state_path=state_path,
            )
            service = OptionPositionManagerService(
                settings=settings,
                paths=paths,
                trading_gateway=MagicMock(),
                logger=MagicMock(spec=logging.Logger),
            )

            with patch.dict(sys.modules, {"Trading": fake_trading_module}):
                service.run_cycle(trading_client=object())

        call_kwargs = fake_manage_current_option_positions.call_args.kwargs
        mock_get_latest_linked_manager_decision.assert_not_called()
        mock_update_manager_decision_latest_pnl.assert_not_called()
        self.assertEqual(call_kwargs["momentum_history_window_size_override"], 10)
        self.assertEqual(call_kwargs["momentum_history_min_samples_override"], 12)
        self.assertEqual(call_kwargs["momentum_history_bad_count_exit_threshold_override"], 11)
        self.assertEqual(call_kwargs["momentum_history_consecutive_bad_exit_threshold_override"], 15)
        self.log_pass("service kept forwarding raw momentum-history settings so the trading layer can normalize impossible combinations centrally")


class OptionManagerCompatibilityTests(VerboseTestCase):
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
        self.log_pass("public option-manager wrapper still delegates directly to the front-main application")


if __name__ == "__main__":
    unittest.main()
