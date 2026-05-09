from __future__ import annotations

import logging
from typing import Any

from db_helpers.market import record_option_trade_execution


class OptionTradeJournal:
    """Persist successful live option executions to the market database."""

    def __init__(self, *, paper: bool, logger: logging.Logger) -> None:
        self._paper = paper
        self._logger = logger

    def record_execution(self, execution: dict[str, Any]) -> int | None:
        record_id = record_option_trade_execution(execution, paper=self._paper)
        if record_id is not None:
            self._logger.info(
                "Recorded executed option trade in DB with id=%s order_id=%s option_symbol=%s",
                record_id,
                execution.get("order", {}).get("id"),
                execution.get("selected_option_symbol"),
            )
        return record_id
