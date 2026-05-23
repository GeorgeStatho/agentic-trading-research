from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from db_helpers.market import (
    expire_manager_decision_pnl,
    get_latest_linked_manager_decision_for_option_symbol,
    update_manager_decision_latest_pnl,
)
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
        trading_gateway: Any,
        trade_journal: Any | None = None,
        logger: logging.Logger,
    ) -> None:
        self._settings = settings
        self._paths = paths
        self._trading_gateway = trading_gateway
        self._trade_journal = trade_journal
        self._logger = logger

    @staticmethod
    def _resolve_position_pnl_pct(position_summary: dict[str, Any]) -> float | None:
        for key in ("broker_unrealized_pl_pct", "unrealized_pl_pct"):
            value = position_summary.get(key)
            try:
                if value in (None, ""):
                    continue
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _resolve_outcome_label(position_summary: dict[str, Any], pnl_pct: float | None) -> str:
        if position_summary.get("close_submitted"):
            if pnl_pct is None:
                return "managed_exit_submitted_unknown"
            if pnl_pct > 0:
                return "managed_exit_submitted_profit"
            if pnl_pct < 0:
                return "managed_exit_submitted_loss"
            return "managed_exit_submitted_flat"
        if pnl_pct is None:
            return "open_unknown"
        if pnl_pct > 0:
            return "open_profit"
        if pnl_pct < 0:
            return "open_loss"
        return "open_flat"

    def _refresh_manager_decision_history_pnl(self, positions: list[dict[str, Any]]) -> None:
        expired_count = expire_manager_decision_pnl()
        if expired_count > 0:
            self._logger.info("Expired P/L context for %s manager decision history records.", expired_count)

        retention_days = max(1, int(getattr(self._settings, "manager_decision_pnl_retention_days", 14)))
        now = datetime.now(timezone.utc)
        pnl_expires_at = now + timedelta(days=retention_days)

        for position_summary in positions:
            if not isinstance(position_summary, dict):
                continue
            option_symbol = str(position_summary.get("symbol") or "").strip().upper()
            if not option_symbol:
                continue
            linked_decision = get_latest_linked_manager_decision_for_option_symbol(
                selected_option_symbol=option_symbol,
            )
            if linked_decision is None:
                continue

            pnl_pct = self._resolve_position_pnl_pct(position_summary)
            outcome_label = self._resolve_outcome_label(position_summary, pnl_pct)
            updated = update_manager_decision_latest_pnl(
                decision_history_id=int(linked_decision["id"]),
                latest_trade_pnl_pct=pnl_pct,
                latest_trade_pnl_updated_at=(
                    position_summary.get("last_action_at")
                    or position_summary.get("option_quote_timestamp")
                    or position_summary.get("underlying_quote_timestamp")
                    or now.isoformat()
                ),
                pnl_expires_at=pnl_expires_at.isoformat(),
                resolved_outcome_label=outcome_label,
            )
            if updated:
                self._logger.info(
                    "Updated manager decision history P/L for %s to %s%%.",
                    option_symbol,
                    pnl_pct,
                )

    def run_cycle(self, *, trading_client: TradingClient) -> dict[str, Any]:
        from Trading import ManageCurrentOptionPositions

        management_result = ManageCurrentOptionPositions(
            execute_sales=self._settings.auto_close_option_positions,
            take_profit_pct=self._settings.option_position_take_profit_pct,
            stop_loss_pct=self._settings.option_position_stop_loss_pct,
            exit_hours_to_expiration=self._settings.option_position_exit_hours_to_expiration,
            state_path_override=self._paths.option_position_state_path,
            enable_trailing_profit_override=self._settings.option_position_enable_trailing_profit,
            trailing_profit_dry_run_override=self._settings.option_position_trailing_profit_dry_run,
            enable_momentum_exit_override=self._settings.option_position_enable_momentum_exit,
            momentum_history_enable_after_pnl_pct_override=self._settings.option_momentum_history_enable_after_pnl_pct,
            momentum_history_window_size_override=self._settings.option_momentum_history_window_size,
            momentum_history_min_samples_override=self._settings.option_momentum_history_min_samples,
            momentum_history_bad_count_exit_threshold_override=self._settings.option_momentum_history_bad_count_exit_threshold,
            momentum_history_consecutive_bad_exit_threshold_override=self._settings.option_momentum_history_consecutive_bad_exit_threshold,
            option_price_momentum_bad_giveback_threshold_override=self._settings.option_price_momentum_bad_giveback_threshold,
            option_price_momentum_mixed_giveback_threshold_override=self._settings.option_price_momentum_mixed_giveback_threshold,
            option_price_momentum_good_profit_threshold_override=self._settings.option_price_momentum_good_profit_threshold,
            trail_protection_trigger_pct_override=self._settings.option_trail_protection_trigger_pct,
            trail_initial_floor_pct_override=self._settings.option_trail_initial_floor_pct,
            trail_first_scale_out_trigger_pct_override=self._settings.option_trail_first_scale_out_trigger_pct,
            trail_first_scale_out_fraction_override=self._settings.option_trail_first_scale_out_fraction,
            trail_second_scale_out_trigger_pct_override=self._settings.option_trail_second_scale_out_trigger_pct,
            trail_second_scale_out_fraction_override=self._settings.option_trail_second_scale_out_fraction,
            trail_giveback_pct_by_bucket_key_override=self._settings.option_trail_giveback_pct_by_bucket_key,
            trail_100_floor_pct_override=self._settings.option_trail_100_floor_pct,
            trail_150_floor_pct_override=self._settings.option_trail_150_floor_pct,
            trail_200_floor_pct_override=self._settings.option_trail_200_floor_pct,
            pending_exit_stale_minutes_override=self._settings.option_pending_exit_stale_minutes,
            pending_exit_cancel_on_stale_override=self._settings.option_pending_exit_cancel_on_stale,
            submit_full_exit_order=lambda option_symbol: self._trading_gateway.close_option_position(
                trading_client,
                option_symbol=option_symbol,
            ),
            submit_partial_exit_order=lambda option_symbol, qty: self._trading_gateway.submit_option_sell_market_order(
                trading_client,
                option_symbol=option_symbol,
                qty=qty,
            ),
            resolve_order_status=lambda order_id: self._trading_gateway.get_order_status_summary(
                trading_client,
                order_id=order_id,
            ),
            cancel_order=lambda order_id: self._trading_gateway.cancel_order(
                trading_client,
                order_id=order_id,
            ),
            trading_client_override=trading_client,
        )

        for position_summary in management_result.get("positions", []):
            if not isinstance(position_summary, dict) or not position_summary.get("close_submitted"):
                continue
            if self._trade_journal is None:
                break
            try:
                record_id = self._trade_journal.record_position_exit(position_summary)
                position_summary["db_recorded"] = record_id is not None
                position_summary["db_record_id"] = record_id
            except Exception as exc:
                self._logger.exception(
                    "Failed to record managed option exit in DB for %s: %s",
                    position_summary.get("symbol"),
                    exc,
                )
                position_summary["db_recorded"] = False
                position_summary["db_record_error"] = str(exc)

        self._refresh_manager_decision_history_pnl(
            management_result.get("positions", [])
            if isinstance(management_result.get("positions", []), list)
            else []
        )

        JsonFileWriter.write(self._paths.option_position_management_output_path, management_result)
        self._logger.info(
            "Managed %s option positions; sell_count=%s full_sell_count=%s partial_sell_count=%s close_submitted_count=%s dry_run=%s",
            management_result.get("position_count"),
            management_result.get("sell_count"),
            management_result.get("full_sell_count"),
            management_result.get("partial_sell_count"),
            management_result.get("close_submitted_count"),
            management_result.get("trailing_profit_dry_run"),
        )
        return management_result
