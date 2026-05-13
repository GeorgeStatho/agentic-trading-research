from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from agentCallers.main import run_full_agent_stack_from_existing_data
from services.cold_start import ColdStartSanityChecker
from services.config import FrontMainPaths, FrontMainSettings
from services.io_utils import JsonFileWriter, StatusReporter
from services.order_candidates import OrderCandidateBuilder
from services.position_manager import OptionPositionManagerService
from services.trade_executor import OptionExposureSnapshot, OptionTradeExecutor
from services.trading_gateway import TradingClient


MARKET_TIMEZONE = ZoneInfo("America/New_York")
FRIDAY_WEEKDAY = 4


class FrontMainApplication:
    """Coordinate agent execution, trading, persistence, and loop scheduling."""

    def __init__(
        self,
        *,
        paths: FrontMainPaths,
        settings: FrontMainSettings,
        status_reporter: StatusReporter,
        cold_start_checker: ColdStartSanityChecker,
        trading_gateway: Any,
        order_candidate_builder: OrderCandidateBuilder,
        trade_executor: OptionTradeExecutor,
        position_manager: OptionPositionManagerService,
        logger: logging.Logger,
    ) -> None:
        # Keep the runtime dependencies assembled here so the loop methods can
        # stay focused on scheduling and trading flow instead of object creation.
        self._paths = paths
        self._settings = settings
        self._status_reporter = status_reporter
        self._cold_start_checker = cold_start_checker
        self._trading_gateway = trading_gateway
        self._order_candidate_builder = order_candidate_builder
        self._trade_executor = trade_executor
        self._position_manager = position_manager
        self._logger = logger

    def _get_option_exposure_snapshot(self, trading_client: TradingClient) -> OptionExposureSnapshot:
        available_buying_power = self._trading_gateway.get_available_buying_power(trading_client)
        current_option_exposure, option_position_count = self._trading_gateway.get_open_option_exposure(
            trading_client
        )
        return OptionExposureSnapshot(
            available_buying_power=available_buying_power,
            max_deployable_buying_power=(
                available_buying_power * self._settings.max_deployable_buying_power_ratio
            ),
            current_option_exposure=current_option_exposure,
            option_position_count=option_position_count,
        )

    def _build_option_exposure_skip_message(self, exposure_snapshot: OptionExposureSnapshot) -> str:
        return (
            f"Skipping trading cycle because current open option exposure "
            f"({exposure_snapshot.current_option_exposure:.2f}) is at or above the configured "
            f"MAX_DEPLOYABLE_BUYING_POWER_PCT cap ({exposure_snapshot.max_deployable_buying_power:.2f}, "
            f"{self._settings.max_deployable_buying_power_pct:.2f}% of account buying power)."
        )

    @staticmethod
    def _market_now() -> datetime:
        return datetime.now(MARKET_TIMEZONE)

    def _is_friday_entry_block(self) -> bool:
        return self._market_now().weekday() == FRIDAY_WEEKDAY

    def _build_friday_entry_skip_message(self) -> str:
        market_now = self._market_now()
        return (
            "Skipping trading cycle because new option entries are disabled on Fridays "
            f"({market_now.date().isoformat()} America/New_York)."
        )

    def _persist_trading_cycle_outputs(
        self,
        *,
        agent_result: dict[str, Any],
        trade_result: dict[str, Any],
        combined_result: dict[str, Any],
    ) -> None:
        JsonFileWriter.write(self._paths.agent_output_path, agent_result)
        JsonFileWriter.write(self._paths.selected_options_output_path, agent_result.get("selected_options", {}))
        JsonFileWriter.write(self._paths.trade_output_path, trade_result)
        JsonFileWriter.write(self._paths.combined_output_path, combined_result)

        self._logger.info("Saved agent output to %s", self._paths.agent_output_path)
        self._logger.info("Saved selected options output to %s", self._paths.selected_options_output_path)
        self._logger.info("Saved trade execution output to %s", self._paths.trade_output_path)
        self._logger.info("Saved combined front-main output to %s", self._paths.combined_output_path)

    @staticmethod
    def _build_combined_result(
        *,
        agent_result: dict[str, Any],
        trade_result: dict[str, Any],
        skipped: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "ran_at": datetime.now().isoformat(),
            "agent_result": agent_result,
            "trade_result": trade_result,
        }
        if skipped:
            payload["skipped"] = True
        return payload

    def _skip_trading_cycle_for_option_exposure(
        self,
        *,
        exposure_snapshot: OptionExposureSnapshot,
    ) -> dict[str, Any]:
        # Build the same persisted output shape as a normal cycle, but mark it
        # skipped so the dashboard and worker status still have a full snapshot.
        skip_reason = self._build_option_exposure_skip_message(exposure_snapshot)
        self._logger.info(skip_reason)

        agent_result = {
            "skipped": True,
            "skip_reason": skip_reason,
            "selected_options": {
                "selected_option_count": 0,
                "companies": [],
            },
        }
        trade_result = {
            "ran_at": datetime.now().isoformat(),
            "paper": self._settings.alpaca_paper,
            "order_qty": self._settings.default_option_order_qty,
            "available_buying_power": exposure_snapshot.available_buying_power,
            "max_deployable_buying_power": exposure_snapshot.max_deployable_buying_power,
            "remaining_deployable_buying_power": exposure_snapshot.remaining_deployable_buying_power,
            "current_option_exposure": exposure_snapshot.current_option_exposure,
            "option_position_count": exposure_snapshot.option_position_count,
            "submitted_count": 0,
            "candidate_count": 0,
            "executions": [],
            "skipped": True,
            "error": skip_reason,
        }
        combined_result = self._build_combined_result(
            agent_result=agent_result,
            trade_result=trade_result,
            skipped=True,
        )

        self._persist_trading_cycle_outputs(
            agent_result=agent_result,
            trade_result=trade_result,
            combined_result=combined_result,
        )
        return combined_result

    def _skip_trading_cycle_for_friday(
        self,
        *,
        exposure_snapshot: OptionExposureSnapshot,
    ) -> dict[str, Any]:
        # Mirror the normal cycle outputs when Friday entry blocking is active
        # so operators can see why no new trades were opened for that session.
        skip_reason = self._build_friday_entry_skip_message()
        self._logger.info(skip_reason)

        agent_result = {
            "skipped": True,
            "skip_reason": skip_reason,
            "selected_options": {
                "selected_option_count": 0,
                "companies": [],
            },
        }
        trade_result = {
            "ran_at": self._market_now().isoformat(),
            "paper": self._settings.alpaca_paper,
            "order_qty": self._settings.default_option_order_qty,
            "available_buying_power": exposure_snapshot.available_buying_power,
            "max_deployable_buying_power": exposure_snapshot.max_deployable_buying_power,
            "remaining_deployable_buying_power": exposure_snapshot.remaining_deployable_buying_power,
            "current_option_exposure": exposure_snapshot.current_option_exposure,
            "option_position_count": exposure_snapshot.option_position_count,
            "submitted_count": 0,
            "candidate_count": 0,
            "executions": [],
            "skipped": True,
            "error": skip_reason,
        }
        combined_result = self._build_combined_result(
            agent_result=agent_result,
            trade_result=trade_result,
            skipped=True,
        )

        self._status_reporter.write(
            "paused",
            "Skipping trading cycle because Friday entry block is active",
            current_option_exposure=exposure_snapshot.current_option_exposure,
            option_position_count=exposure_snapshot.option_position_count,
            max_deployable_buying_power=exposure_snapshot.max_deployable_buying_power,
            remaining_deployable_buying_power=exposure_snapshot.remaining_deployable_buying_power,
            market_date=self._market_now().date().isoformat(),
            market_timezone="America/New_York",
        )
        self._persist_trading_cycle_outputs(
            agent_result=agent_result,
            trade_result=trade_result,
            combined_result=combined_result,
        )
        return combined_result

    def run_trading_cycle(
        self,
        trading_client: TradingClient | None = None,
        *,
        exposure_snapshot: OptionExposureSnapshot | None = None,
    ) -> dict[str, Any]:
        """Run one end-to-end agent + trading cycle."""
        # This is the batch path: run the agent stack, convert its selections
        # into order candidates, execute them, then persist the full cycle outputs.
        trading_client = trading_client or self._trading_gateway.create_client()
        exposure_snapshot = exposure_snapshot or self._get_option_exposure_snapshot(trading_client)
        if self._is_friday_entry_block():
            return self._skip_trading_cycle_for_friday(exposure_snapshot=exposure_snapshot)
        if exposure_snapshot.exceeds_max_exposure:
            return self._skip_trading_cycle_for_option_exposure(exposure_snapshot=exposure_snapshot)

        self._logger.info("Starting full agent stack run")
        agent_result = run_full_agent_stack_from_existing_data()
        self._logger.info("Finished full agent stack run")

        order_candidates = self._order_candidate_builder.build(agent_result)
        self._logger.info("Prepared %s selected option candidates for trading", len(order_candidates))

        trade_result = self._trade_executor.execute(
            trading_client=trading_client,
            order_candidates=order_candidates,
        )

        combined_result = self._build_combined_result(
            agent_result=agent_result,
            trade_result=trade_result,
        )

        self._persist_trading_cycle_outputs(
            agent_result=agent_result,
            trade_result=trade_result,
            combined_result=combined_result,
        )

        return combined_result

    def run_streaming_trading_cycle(
        self,
        trading_client: TradingClient | None = None,
        *,
        exposure_snapshot: OptionExposureSnapshot | None = None,
    ) -> dict[str, Any]:
        """Run the agent stack and execute qualifying options immediately per manager result."""
        # This path keeps one shared trade session open and lets each manager
        # result trigger an immediate execution before the full agent run completes.
        trading_client = trading_client or self._trading_gateway.create_client()
        exposure_snapshot = exposure_snapshot or self._get_option_exposure_snapshot(trading_client)
        if self._is_friday_entry_block():
            return self._skip_trading_cycle_for_friday(exposure_snapshot=exposure_snapshot)
        if exposure_snapshot.exceeds_max_exposure:
            return self._skip_trading_cycle_for_option_exposure(exposure_snapshot=exposure_snapshot)

        trade_session = self._trade_executor.create_session(trading_client=trading_client)

        def handle_manager_result(manager_result: dict[str, Any]) -> None:
            candidate = self._order_candidate_builder.build_from_manager_result(manager_result)
            if candidate is None:
                return

            execution = self._trade_executor.execute_candidate(
                trading_client=trading_client,
                candidate=candidate,
                session=trade_session,
            )
            self._logger.info(
                "Immediate option execution for %s completed with submitted=%s error=%s",
                candidate.get("symbol"),
                execution.get("submitted"),
                execution.get("error"),
            )
            JsonFileWriter.write(self._paths.trade_output_path, self._trade_executor.finalize_session(trade_session))

        self._logger.info("Starting full agent stack run with immediate option execution")
        agent_result = run_full_agent_stack_from_existing_data(on_manager_result=handle_manager_result)
        self._logger.info("Finished full agent stack run with immediate option execution")

        trade_result = self._trade_executor.finalize_session(trade_session)
        combined_result = self._build_combined_result(
            agent_result=agent_result,
            trade_result=trade_result,
        )

        self._persist_trading_cycle_outputs(
            agent_result=agent_result,
            trade_result=trade_result,
            combined_result=combined_result,
        )

        return combined_result

    def run_option_position_management_cycle(self, *, trading_client: TradingClient) -> dict[str, Any]:
        """Run one option-position management cycle."""
        return self._position_manager.run_cycle(trading_client=trading_client)

    def _sleep_for_market_close(self, *, loop_label: str, loop_started_at: datetime | None = None) -> None:
        # Centralize the "market closed" pause so both long-running loops report
        # the same status payload before backing off for the configured interval.
        if loop_label == "option manager":
            message = "Market is closed for dedicated option manager."
        else:
            message = "Market is closed."

        self._logger.info(
            "%s Sleeping %s seconds before checking again.",
            message,
            self._settings.market_recheck_seconds,
        )
        status_payload: dict[str, Any] = {
            "sleep_seconds": self._settings.market_recheck_seconds,
        }
        if loop_started_at is None:
            status_payload["next_check_at"] = datetime.now().isoformat()
        else:
            status_payload["last_loop_started_at"] = loop_started_at.isoformat()

        self._status_reporter.write("paused", "Market is closed", **status_payload)
        time.sleep(self._settings.market_recheck_seconds)

    def _run_due_option_position_management(
        self,
        *,
        trading_client: TradingClient,
        next_option_management_at: datetime,
        loop_log_prefix: str,
        loop_exception_label: str,
    ) -> datetime:
        # Run the option-manager side task only when its own timer is due, then
        # always return the next scheduled run so the outer loop stays simple.
        current_time = datetime.now()
        if not self._settings.auto_manage_option_positions or current_time < next_option_management_at:
            return next_option_management_at

        try:
            self._status_reporter.write("running", "Managing current option positions")
            option_management_result = self.run_option_position_management_cycle(
                trading_client=trading_client
            )
            self._logger.info(
                "%s finished with %s tracked positions.",
                loop_log_prefix,
                option_management_result.get("position_count"),
            )
        except Exception as exc:
            self._status_reporter.write("error", f"Option position management failed: {exc}")
            self._logger.exception("%s failed: %s", loop_exception_label, exc)
        return datetime.now() + timedelta(
            seconds=self._settings.option_position_management_interval_seconds
        )

    def _run_scheduled_trading_cycle(self, *, trading_client: TradingClient) -> dict[str, Any]:
        # Reuse one exposure snapshot to decide whether to pause, stream trades
        # immediately, or run the standard batch cycle for this scheduled slot.
        exposure_snapshot = self._get_option_exposure_snapshot(trading_client)
        if exposure_snapshot.exceeds_max_exposure:
            result = self._skip_trading_cycle_for_option_exposure(
                exposure_snapshot=exposure_snapshot
            )
            self._status_reporter.write(
                "paused",
                "Skipping trading cycle because option exposure cap is already consumed",
                current_option_exposure=exposure_snapshot.current_option_exposure,
                option_position_count=exposure_snapshot.option_position_count,
                max_deployable_buying_power=exposure_snapshot.max_deployable_buying_power,
                remaining_deployable_buying_power=(
                    exposure_snapshot.remaining_deployable_buying_power
                ),
            )
            return result

        if self._settings.immediate_option_execution:
            self._status_reporter.write(
                "running",
                "Executing trading cycle with immediate option execution",
            )
            return self.run_streaming_trading_cycle(
                trading_client=trading_client,
                exposure_snapshot=exposure_snapshot,
            )

        self._status_reporter.write("running", "Executing trading cycle")
        return self.run_trading_cycle(
            trading_client=trading_client,
            exposure_snapshot=exposure_snapshot,
        )

    def run_main_loop(self) -> None:
        """Run the full scheduled loop that alternates trading and position management."""
        # Drive the all-in-one worker: wait for market-open windows, interleave
        # trading cycles with position management, and publish status on each pass.
        trading_client = self._trading_gateway.create_client()
        next_trading_cycle_at = datetime.now()
        next_option_management_at = datetime.now()

        self._logger.info(
            "Starting front-facing main loop with interval=%s seconds, option management interval=%s seconds, market recheck=%s seconds, and immediate_option_execution=%s",
            self._settings.run_interval_seconds,
            self._settings.option_position_management_interval_seconds,
            self._settings.market_recheck_seconds,
            self._settings.immediate_option_execution,
        )
        self._status_reporter.write(
            "starting",
            "Front-facing main loop started",
            run_interval_seconds=self._settings.run_interval_seconds,
            option_position_management_interval_seconds=self._settings.option_position_management_interval_seconds,
            market_recheck_seconds=self._settings.market_recheck_seconds,
            immediate_option_execution=self._settings.immediate_option_execution,
        )

        if self._settings.cold_start_sanity_check_enabled:
            self._run_cold_start_sanity_check()

        while True:
            loop_started_at = datetime.now()

            if not self._trading_gateway.market_is_open(trading_client):
                self._sleep_for_market_close(loop_label="front main")
                continue

            next_option_management_at = self._run_due_option_position_management(
                trading_client=trading_client,
                next_option_management_at=next_option_management_at,
                loop_log_prefix="Option management cycle",
                loop_exception_label="Option position management",
            )

            current_time = datetime.now()
            if current_time >= next_trading_cycle_at:
                try:
                    result = self._run_scheduled_trading_cycle(trading_client=trading_client)
                    print(json.dumps(result, ensure_ascii=True, indent=2))
                except Exception as exc:
                    self._status_reporter.write("error", f"Front-facing main cycle failed: {exc}")
                    self._logger.exception("Front-facing main cycle failed: %s", exc)
                finally:
                    next_trading_cycle_at = datetime.now() + timedelta(seconds=self._settings.run_interval_seconds)

            sleep_seconds = self._compute_next_sleep_seconds(
                next_trading_cycle_at=next_trading_cycle_at,
                next_option_management_at=next_option_management_at,
            )
            self._logger.info("Loop iteration complete. Sleeping %.1f seconds until next task.", sleep_seconds)
            self._status_reporter.write(
                "paused",
                "Sleeping until next task",
                sleep_seconds=sleep_seconds,
                last_loop_started_at=loop_started_at.isoformat(),
                next_trading_cycle_at=next_trading_cycle_at.isoformat(),
                next_option_management_at=(
                    next_option_management_at.isoformat()
                    if self._settings.auto_manage_option_positions
                    else ""
                ),
            )
            time.sleep(sleep_seconds)

    def run_option_manager_loop(self) -> None:
        """Run the dedicated option-manager loop for existing option positions only."""
        # This lighter loop exists for deployments that only need position
        # management; it never opens new trades and wakes on its own schedule.
        trading_client = self._trading_gateway.create_client()
        next_option_management_at = datetime.now()

        self._logger.info(
            "Starting dedicated option manager loop with option management interval=%s seconds and market recheck=%s seconds",
            self._settings.option_position_management_interval_seconds,
            self._settings.market_recheck_seconds,
        )
        self._status_reporter.write(
            "starting",
            "Dedicated option manager loop started",
            option_position_management_interval_seconds=self._settings.option_position_management_interval_seconds,
            market_recheck_seconds=self._settings.market_recheck_seconds,
            auto_manage_option_positions=self._settings.auto_manage_option_positions,
            auto_close_option_positions=self._settings.auto_close_option_positions,
        )

        if self._settings.cold_start_sanity_check_enabled:
            self._run_cold_start_sanity_check()

        if not self._settings.auto_manage_option_positions:
            self._logger.warning(
                "AUTO_MANAGE_OPTION_POSITIONS is disabled; dedicated option manager will remain idle aside from market-open checks."
            )

        while True:
            loop_started_at = datetime.now()

            if not self._trading_gateway.market_is_open(trading_client):
                self._sleep_for_market_close(
                    loop_label="option manager",
                    loop_started_at=loop_started_at,
                )
                continue

            next_option_management_at = self._run_due_option_position_management(
                trading_client=trading_client,
                next_option_management_at=next_option_management_at,
                loop_log_prefix="Dedicated option manager",
                loop_exception_label="Dedicated option manager",
            )

            current_time = datetime.now()
            next_wake_targets = [datetime.now() + timedelta(seconds=self._settings.market_recheck_seconds)]
            if self._settings.auto_manage_option_positions:
                next_wake_targets.append(next_option_management_at)
            sleep_seconds = max(0.0, min((target - current_time).total_seconds() for target in next_wake_targets))
            self._logger.info(
                "Dedicated option manager sleeping %.1f seconds until next task.",
                sleep_seconds,
            )
            self._status_reporter.write(
                "paused",
                (
                    "Sleeping until next task"
                    if self._settings.auto_manage_option_positions
                    else "Option management disabled; sleeping until next market check"
                ),
                sleep_seconds=sleep_seconds,
                last_loop_started_at=loop_started_at.isoformat(),
                next_option_management_at=(
                    next_option_management_at.isoformat()
                    if self._settings.auto_manage_option_positions
                    else ""
                ),
            )
            time.sleep(sleep_seconds)

    def _run_cold_start_sanity_check(self) -> None:
        try:
            sanity_result = self._cold_start_checker.run()
            self._logger.info("Cold-start sanity check passed: %s", sanity_result)
            self._status_reporter.write(
                "starting",
                "Cold-start sanity check passed",
                run_interval_seconds=self._settings.run_interval_seconds,
                option_position_management_interval_seconds=self._settings.option_position_management_interval_seconds,
                market_recheck_seconds=self._settings.market_recheck_seconds,
                cold_start_sanity_check=sanity_result,
            )
        except Exception as exc:
            self._status_reporter.write("error", f"Cold-start sanity check failed: {exc}")
            self._logger.exception("Cold-start sanity check failed: %s", exc)
            raise

    def _compute_next_sleep_seconds(
        self,
        *,
        next_trading_cycle_at: datetime,
        next_option_management_at: datetime,
    ) -> float:
        next_wake_targets = [datetime.now() + timedelta(seconds=self._settings.market_recheck_seconds)]
        if self._settings.auto_manage_option_positions:
            next_wake_targets.append(next_option_management_at)
        next_wake_targets.append(next_trading_cycle_at)

        current_time = datetime.now()
        return max(0.0, min((target - current_time).total_seconds() for target in next_wake_targets))
