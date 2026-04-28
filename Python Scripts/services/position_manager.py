from __future__ import annotations

import logging
from typing import Any

from services.config import FrontMainPaths, FrontMainSettings
from services.io_utils import JsonFileWriter
from services.trading_gateway import TradingClient


class OptionPositionManagerService:
    """Run the existing option-position maintenance workflow and persist its output."""

    def __init__(
        self,
        *,
        settings: FrontMainSettings,
        paths: FrontMainPaths,
        logger: logging.Logger,
    ) -> None:
        self._settings = settings
        self._paths = paths
        self._logger = logger

    def run_cycle(self, *, trading_client: TradingClient) -> dict[str, Any]:
        from Trading import ManageCurrentOptionPositions

        management_result = ManageCurrentOptionPositions(
            execute_sales=self._settings.auto_close_option_positions,
            take_profit_pct=self._settings.option_position_take_profit_pct,
            stop_loss_pct=self._settings.option_position_stop_loss_pct,
            exit_hours_to_expiration=self._settings.option_position_exit_hours_to_expiration,
            trading_client_override=trading_client,
        )

        JsonFileWriter.write(self._paths.option_position_management_output_path, management_result)
        self._logger.info(
            "Managed %s option positions; sell_count=%s close_submitted_count=%s",
            management_result.get("position_count"),
            management_result.get("sell_count"),
            management_result.get("close_submitted_count"),
        )
        return management_result
