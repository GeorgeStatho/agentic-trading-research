from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.worker_application import WorkerApplication  # noqa: E402


class StartupReadinessIntegrationTests(unittest.TestCase):
    def test_front_main_loop_skips_new_trading_cycles_during_market_open_cooldown(self):
        trading_client = object()
        trading_gateway = MagicMock()
        trading_gateway.create_client.return_value = trading_client
        trading_gateway.get_market_clock.return_value = SimpleNamespace(
            is_open=True,
            timestamp="2026-05-16T13:31:00Z",
        )
        trading_gateway.market_is_open.return_value = True
        status_reporter = MagicMock()

        app = WorkerApplication(
            paths=SimpleNamespace(),
            settings=SimpleNamespace(
                run_interval_seconds=60,
                option_position_management_interval_seconds=60,
                market_recheck_seconds=300,
                main_loop_entry_cooldown_seconds_after_market_open=300,
                immediate_option_execution=False,
                cold_start_sanity_check_enabled=False,
                auto_manage_option_positions=False,
            ),
            status_reporter=status_reporter,
            cold_start_checker=MagicMock(),
            trading_gateway=trading_gateway,
            order_candidate_builder=MagicMock(),
            trade_executor=MagicMock(),
            position_manager=MagicMock(),
            logger=MagicMock(spec=logging.Logger),
        )

        with patch.object(app, "_run_startup_readiness_check"):
            with patch.object(app, "_run_scheduled_trading_cycle") as mock_run_scheduled_trading_cycle:
                with patch.object(app, "_compute_next_sleep_seconds", return_value=1.0):
                    with patch("services.worker_application.time.sleep", side_effect=RuntimeError("stop-loop")):
                        with self.assertRaisesRegex(RuntimeError, "stop-loop"):
                            app.run_main_loop()

        mock_run_scheduled_trading_cycle.assert_not_called()
        self.assertTrue(
            any(
                call.args[:2]
                == (
                    "paused",
                    "Skipping trading cycle because market-open entry cooldown is active",
                )
                and call.kwargs.get("stage") == "market_open_entry_cooldown"
                for call in status_reporter.write.call_args_list
            )
        )

    def test_front_main_loop_runs_new_trading_cycles_after_market_open_cooldown(self):
        trading_client = object()
        trading_gateway = MagicMock()
        trading_gateway.create_client.return_value = trading_client
        trading_gateway.get_market_clock.return_value = SimpleNamespace(
            is_open=True,
            timestamp="2026-05-16T13:36:00Z",
        )
        trading_gateway.market_is_open.return_value = True

        app = WorkerApplication(
            paths=SimpleNamespace(),
            settings=SimpleNamespace(
                run_interval_seconds=60,
                option_position_management_interval_seconds=60,
                market_recheck_seconds=300,
                main_loop_entry_cooldown_seconds_after_market_open=300,
                immediate_option_execution=False,
                cold_start_sanity_check_enabled=False,
                auto_manage_option_positions=False,
            ),
            status_reporter=MagicMock(),
            cold_start_checker=MagicMock(),
            trading_gateway=trading_gateway,
            order_candidate_builder=MagicMock(),
            trade_executor=MagicMock(),
            position_manager=MagicMock(),
            logger=MagicMock(spec=logging.Logger),
        )

        with patch.object(app, "_run_startup_readiness_check"):
            with patch.object(app, "_run_scheduled_trading_cycle", return_value={"ok": True}) as mock_run:
                with patch.object(app, "_compute_next_sleep_seconds", return_value=1.0):
                    with patch("services.worker_application.time.sleep", side_effect=RuntimeError("stop-loop")):
                        with self.assertRaisesRegex(RuntimeError, "stop-loop"):
                            app.run_main_loop()

        mock_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
