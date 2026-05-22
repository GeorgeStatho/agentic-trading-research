from __future__ import annotations

import logging
import os
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

import news_collector_main  # noqa: E402
from services.front_main_application import FrontMainApplication  # noqa: E402
from services.startup_readiness import StartupReadinessChecker  # noqa: E402


class StartupReadinessCheckerTests(unittest.TestCase):
    @patch("services.startup_readiness.ask_llm_model")
    @patch("services.startup_readiness.get_model_client")
    def test_run_executes_output_llm_and_alpaca_checks(self, mock_get_model_client, mock_ask_llm_model):
        logger = MagicMock(spec=logging.Logger)
        checker = StartupReadinessChecker(logger)

        trading_client = MagicMock()
        trading_client.get_account.return_value = SimpleNamespace(
            status="ACTIVE",
            buying_power="1234.56",
        )
        trading_client.get_clock.return_value = SimpleNamespace(
            is_open=True,
            timestamp="2026-05-16T13:30:00Z",
        )
        trading_gateway = MagicMock()
        trading_gateway.create_client.return_value = trading_client
        mock_get_model_client.return_value = SimpleNamespace(provider="vertex")
        mock_ask_llm_model.return_value = "READY"

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "runtime" / "startup_status.json"
            with patch.dict(os.environ, {"STARTUP_READINESS_CHECK": "true"}, clear=False):
                result = checker.run(
                    runtime_name="worker",
                    output_paths=(output_path,),
                    require_llm=True,
                    require_alpaca=True,
                    trading_gateway=trading_gateway,
                    alpaca_paper=True,
                )

        self.assertEqual(result["runtime"], "worker")
        self.assertTrue(result["enabled"])
        self.assertEqual(
            [check["name"] for check in result["checks"]],
            ["output_path", "llm", "alpaca"],
        )
        trading_gateway.create_client.assert_called_once()
        trading_client.get_account.assert_called_once()
        trading_client.get_clock.assert_called_once()
        mock_get_model_client.assert_called_once()
        mock_ask_llm_model.assert_called_once()

    def test_run_skips_all_checks_when_disabled(self):
        logger = MagicMock(spec=logging.Logger)
        checker = StartupReadinessChecker(logger)

        with patch.dict(os.environ, {"STARTUP_READINESS_CHECK": "false"}, clear=False):
            result = checker.run(
                runtime_name="news_collector",
                output_paths=(),
                require_llm=True,
                require_alpaca=False,
            )

        self.assertEqual(
            result,
            {
                "runtime": "news_collector",
                "enabled": False,
                "skipped": True,
                "checks": [],
            },
        )


class StartupReadinessIntegrationTests(unittest.TestCase):
    def test_front_main_loop_runs_startup_readiness_once_before_looping(self):
        trading_gateway = MagicMock()
        trading_gateway.create_client.return_value = object()
        trading_gateway.market_is_open.return_value = False
        app = FrontMainApplication(
            paths=SimpleNamespace(),
            settings=SimpleNamespace(
                run_interval_seconds=60,
                option_position_management_interval_seconds=60,
                market_recheck_seconds=300,
                main_loop_max_seconds_after_market_open=None,
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

        with patch.object(app, "_run_startup_readiness_check") as mock_startup_check:
            with patch.object(app, "_sleep_for_market_close", side_effect=RuntimeError("stop-loop")):
                with self.assertRaisesRegex(RuntimeError, "stop-loop"):
                    app.run_main_loop()

        mock_startup_check.assert_called_once_with()
        trading_gateway.create_client.assert_called_once()

    def test_front_main_loop_skips_new_trading_cycles_after_configured_post_open_window(self):
        trading_client = object()
        trading_gateway = MagicMock()
        trading_gateway.create_client.return_value = trading_client
        trading_gateway.get_market_clock.return_value = SimpleNamespace(
            is_open=True,
            timestamp="2026-05-16T15:30:00Z",
        )
        trading_gateway.market_is_open.return_value = True
        status_reporter = MagicMock()
        app = FrontMainApplication(
            paths=SimpleNamespace(),
            settings=SimpleNamespace(
                run_interval_seconds=60,
                option_position_management_interval_seconds=60,
                market_recheck_seconds=300,
                main_loop_max_seconds_after_market_open=300,
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
                    with patch("services.front_main_application.time.sleep", side_effect=RuntimeError("stop-loop")):
                        with self.assertRaisesRegex(RuntimeError, "stop-loop"):
                            app.run_main_loop()

        mock_run_scheduled_trading_cycle.assert_not_called()
        self.assertTrue(
            any(
                call.args[:2]
                == (
                    "paused",
                    "Skipping trading cycle because configured post-open trading window has elapsed",
                )
                and call.kwargs.get("stage") == "trading_window_closed"
                for call in status_reporter.write.call_args_list
            )
        )

    def test_news_collector_loop_runs_startup_readiness_once_before_sleeping(self):
        fake_settings = SimpleNamespace(
            interval_seconds=3600,
            cold_start_sanity_check_enabled=False,
            output_path=Path("/tmp/news_startup_output.json"),
        )

        with patch.object(news_collector_main, "SETTINGS", fake_settings):
            with patch.object(news_collector_main, "_run_startup_readiness_check") as mock_startup_check:
                with patch.object(
                    news_collector_main,
                    "run_news_collection_cycle",
                    return_value={"sectors": [], "pipeline_mode": "test"},
                ):
                    with patch.object(news_collector_main.time, "sleep", side_effect=RuntimeError("stop-loop")):
                        with self.assertRaisesRegex(RuntimeError, "stop-loop"):
                            news_collector_main.main_loop()

        mock_startup_check.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
