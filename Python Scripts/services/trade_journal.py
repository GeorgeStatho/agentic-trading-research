from __future__ import annotations

import logging
from typing import Any

from db_helpers.market import record_option_trade_execution


class OptionTradeJournal:
    """Persist successful live option executions to the market database."""

    def __init__(self, *, paper: bool, logger: logging.Logger) -> None:
        self._paper = paper
        self._logger = logger

    def _record(self, execution: dict[str, Any], *, log_label: str) -> int | None:
        record_id = record_option_trade_execution(execution, paper=self._paper)
        if record_id is not None:
            self._logger.info(
                "Recorded %s option trade in DB with id=%s order_id=%s option_symbol=%s",
                log_label,
                record_id,
                execution.get("order", {}).get("id"),
                execution.get("selected_option_symbol"),
            )
        return record_id

    def record_execution(self, execution: dict[str, Any]) -> int | None:
        return self._record(execution, log_label="executed")

    def record_position_exit(self, position_summary: dict[str, Any]) -> int | None:
        if not isinstance(position_summary, dict):
            return None
        if not position_summary.get("close_submitted"):
            return None

        option_symbol = str(position_summary.get("symbol") or "").strip().upper()
        if not option_symbol:
            return None

        order_id = str(position_summary.get("close_order_id") or "").strip()
        if not order_id:
            return None

        requested_qty = position_summary.get("close_requested_qty") or position_summary.get("qty_to_sell")
        execution = {
            "submitted": True,
            "symbol": str(position_summary.get("underlying_symbol") or "").strip().upper(),
            "name": str(position_summary.get("underlying_symbol") or "").strip().upper(),
            "selected_option_symbol": option_symbol,
            "decision": str(position_summary.get("exit_reason") or position_summary.get("exit_action") or "").strip(),
            "confidence": "managed_exit",
            "selected_option_id": None,
            "selected_option_source": "position_manager",
            "selected_expiration_date": position_summary.get("expiration_date"),
            "selected_strike_price": position_summary.get("strike"),
            "order_qty": requested_qty,
            "estimated_order_cost": None,
            "available_buying_power": None,
            "max_deployable_buying_power": None,
            "remaining_deployable_buying_power": None,
            "submitted_at": position_summary.get("last_action_at"),
            "order": {
                "id": order_id,
                "symbol": option_symbol,
                "qty": requested_qty,
                "status": str(position_summary.get("close_order_status") or "").strip(),
                "side": str(position_summary.get("close_order_side") or "sell").strip(),
                "type": "market",
                "time_in_force": "day",
                "submitted_at": position_summary.get("last_action_at"),
            },
            "raw_position_summary": position_summary,
        }
        return self._record(execution, log_label="managed exit")
