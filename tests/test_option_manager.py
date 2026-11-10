from __future__ import annotations

import importlib.util
from contextlib import closing
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"

from services.core.trading_config import OptionPositionSettings  # noqa: E402
from services.options.option_dte_buckets import OPTION_DTE_BUCKETS  # noqa: E402
from services.options.position_manager import OptionPositionManagerService  # noqa: E402
from services.thesis.thesis_capture import PositionThesisJournal  # noqa: E402
from db_common import get_connection  # noqa: E402
from tests.db_fixtures import temporary_database  # noqa: E402


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


def _build_option_manager_env_values(bucket_giveback_fixture: dict[str, float], **overrides) -> dict[str, object]:
    values: dict[str, object] = {
        "AUTO_CLOSE_OPTION_POSITIONS": True,
        "OPTION_POSITION_TAKE_PROFIT_PCT": 25.0,
        "OPTION_POSITION_STOP_LOSS_PCT": -20.0,
        "OPTION_POSITION_EXIT_HOURS_TO_EXPIRATION": 24.0,
        "OPTION_POSITION_ENABLE_TRAILING_PROFIT": True,
        "OPTION_POSITION_TRAILING_PROFIT_DRY_RUN": False,
        "OPTION_POSITION_ENABLE_MOMENTUM_EXIT": True,
        "OPTION_MOMENTUM_HISTORY_ENABLE_AFTER_PNL_PCT": 2.00,
        "OPTION_MOMENTUM_HISTORY_WINDOW_SIZE": 10,
        "OPTION_MOMENTUM_HISTORY_MIN_SAMPLES": 5,
        "OPTION_MOMENTUM_HISTORY_BAD_COUNT_EXIT_THRESHOLD": 6,
        "OPTION_MOMENTUM_HISTORY_CONSECUTIVE_BAD_EXIT_THRESHOLD": 3,
        "OPTION_PRICE_MOMENTUM_BAD_GIVEBACK_THRESHOLD": 0.40,
        "OPTION_PRICE_MOMENTUM_MIXED_GIVEBACK_THRESHOLD": 0.20,
        "OPTION_PRICE_MOMENTUM_GOOD_PROFIT_THRESHOLD": 0.12,
        "OPTION_TRAIL_PROTECTION_TRIGGER_PCT": 0.40,
        "OPTION_TRAIL_INITIAL_FLOOR_PCT": 0.10,
        "OPTION_TRAIL_FIRST_SCALE_OUT_TRIGGER_PCT": 0.55,
        "OPTION_TRAIL_FIRST_SCALE_OUT_FRACTION": 0.50,
        "OPTION_TRAIL_SECOND_SCALE_OUT_TRIGGER_PCT": 2.00,
        "OPTION_TRAIL_SECOND_SCALE_OUT_FRACTION": 0.25,
        "OPTION_TRAIL_100_FLOOR_PCT": 0.60,
        "OPTION_TRAIL_150_FLOOR_PCT": 1.00,
        "OPTION_TRAIL_200_FLOOR_PCT": 1.40,
        "OPTION_PENDING_EXIT_STALE_MINUTES": 10.0,
        "OPTION_PENDING_EXIT_CANCEL_ON_STALE": True,
        "MANAGER_DECISION_PNL_RETENTION_DAYS": 14,
    }
    for bucket_key, value in bucket_giveback_fixture.items():
        values[f"OPTION_TRAIL_{str(bucket_key).upper()}_GIVEBACK_PCT"] = value
    values.update(overrides)
    return values


def _build_bucket_giveback_fixture() -> dict[str, float]:
    base_value = 0.30
    step = 0.05
    return {
        bucket.key: round(base_value + (index * step), 2)
        for index, bucket in enumerate(OPTION_DTE_BUCKETS, start=1)
    }


def _build_position_manager_settings(bucket_giveback_fixture: dict[str, float], **overrides) -> SimpleNamespace:
    env_values = _build_option_manager_env_values(bucket_giveback_fixture)
    with patch.dict("os.environ", {name: str(value) for name, value in env_values.items()}):
        option_positions = OptionPositionSettings.from_env()
    return SimpleNamespace(**{
        "auto_close_option_positions": env_values["AUTO_CLOSE_OPTION_POSITIONS"],
        "option_positions": option_positions,
        "manager_decision_pnl_retention_days": env_values["MANAGER_DECISION_PNL_RETENTION_DAYS"],
        **overrides,
    })


class OptionPositionManagerServiceTests(VerboseTestCase):
    @patch("services.options.position_manager.update_manager_decision_latest_pnl")
    @patch("services.options.position_manager.get_latest_linked_manager_decision_for_option_symbol")
    @patch("services.options.position_manager.expire_manager_decision_pnl")
    @patch("services.options.position_manager.JsonFileWriter.write")
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

            with patch("trading_support.option_positions.ManageCurrentOptionPositions",
                       fake_manage_current_option_positions):
                service.run_cycle(trading_client=object())

        mock_json_write.assert_called_once_with(output_path, management_result)
        manage_kwargs = fake_manage_current_option_positions.call_args.kwargs
        self.assertIs(manage_kwargs["settings"], settings.option_positions)
        self.assertEqual(manage_kwargs["state_path"], state_path)
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

    @patch("services.options.position_manager.update_manager_decision_latest_pnl")
    @patch("services.options.position_manager.get_latest_linked_manager_decision_for_option_symbol")
    @patch("services.options.position_manager.expire_manager_decision_pnl")
    @patch("services.options.position_manager.JsonFileWriter.write")
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

            with patch("trading_support.option_positions.ManageCurrentOptionPositions",
                       fake_manage_current_option_positions):
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


class PositionThesisLifecycleTests(VerboseTestCase):
    """A thesis is retired when the broker no longer holds its contract, not when an exit is submitted."""

    HELD = "MSFT260619C00450000"

    def setUp(self):
        temporary_database(self)
        for patcher in (patch("services.options.position_manager.update_manager_decision_latest_pnl"),
                        patch("services.options.position_manager.get_latest_linked_manager_decision_for_option_symbol",
                              return_value=None),
                        patch("services.options.position_manager.expire_manager_decision_pnl", return_value=0),
                        patch("services.options.position_manager.JsonFileWriter.write")):
            patcher.start()
            self.addCleanup(patcher.stop)
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.paths = SimpleNamespace(
            option_position_management_output_path=Path(temp_dir.name) / "out.json",
            option_position_state_path=Path(temp_dir.name) / "state.json",
        )

    def _add_thesis(self, option_symbol, *, status="open"):
        with closing(get_connection()) as conn, conn:
            conn.execute("""INSERT INTO position_theses (symbol, option_symbol, opened_at, status, closed_at)
                VALUES ('MSFT', ?, '2026-05-01T14:00:00+00:00', ?, ?)""",
                         (option_symbol, status, "2026-05-20T15:00:00+00:00" if status == "closed" else None))

    def _status(self, option_symbol):
        with closing(get_connection()) as conn:
            return [row[0] for row in conn.execute(
                "SELECT status FROM position_theses WHERE option_symbol=? ORDER BY id", (option_symbol,))]

    def _run(self, positions, flattened=()):
        result = {"positions": positions, "flattened_symbols": list(flattened), "position_count": len(positions)}
        service = OptionPositionManagerService(
            settings=_build_position_manager_settings(_build_bucket_giveback_fixture()),
            paths=self.paths, trading_gateway=MagicMock(), trade_journal=MagicMock(),
            thesis_journal=PositionThesisJournal(), logger=MagicMock(spec=logging.Logger))
        with patch("trading_support.option_positions.ManageCurrentOptionPositions", MagicMock(return_value=result)):
            service.run_cycle(trading_client=object())

    def test_a_partial_scale_out_keeps_the_thesis_open(self):
        self._add_thesis(self.HELD)
        self._run([{"symbol": self.HELD, "close_submitted": True, "exit_action": "sell_partial"}])
        self.assertEqual(["open"], self._status(self.HELD))

    def test_a_submitted_full_exit_that_has_not_flattened_keeps_the_thesis_open(self):
        self._add_thesis(self.HELD)
        self._run([{"symbol": self.HELD, "close_submitted": True, "exit_action": "sell_full"}])
        self.assertEqual(["open"], self._status(self.HELD))

    def test_a_contract_no_longer_held_closes_its_thesis(self):
        self._add_thesis(self.HELD)
        self._run([], flattened=[self.HELD])
        self.assertEqual(["closed"], self._status(self.HELD))

    def test_a_contract_never_seen_held_keeps_its_thesis_open(self):
        self._add_thesis(self.HELD)
        self._run([])
        self.assertEqual(["open"], self._status(self.HELD))

    def test_a_held_contract_whose_thesis_was_retired_early_is_reopened(self):
        self._add_thesis(self.HELD, status="closed")
        self._run([{"symbol": self.HELD, "close_submitted": False}])
        self.assertEqual(["open"], self._status(self.HELD))

    def test_a_held_contract_with_an_open_thesis_reopens_nothing_else(self):
        self._add_thesis(self.HELD, status="closed")
        self._add_thesis(self.HELD)
        self._run([{"symbol": self.HELD, "close_submitted": False}])
        self.assertEqual(["closed", "open"], self._status(self.HELD))


if __name__ == "__main__":
    unittest.main()
