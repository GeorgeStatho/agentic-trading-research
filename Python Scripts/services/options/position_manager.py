from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from market_db_modules.history_records import (
    expire_manager_decision_pnl,
    update_manager_decision_latest_pnl,
)
from market_db_modules.history_links import get_latest_linked_manager_decision_for_option_symbol
from services.core.trading_config import WorkerPaths, WorkerSettings
from services.core.status_files import JsonFileWriter
from services.trading.trading_gateway import TradingClient


class OptionPositionManagerService:
    """Run the existing option-position maintenance workflow and persist its output."""

    def __init__(
        self,
        *,
        settings: WorkerSettings,
        paths: WorkerPaths,
        trading_gateway: Any,
        trade_journal: Any | None = None,
        thesis_journal: Any | None = None,
        thesis_validator: Any | None = None,
        event_thesis_reviewer: Any | None = None,
        logger: logging.Logger,
    ) -> None:
        self._settings = settings
        self._paths = paths
        self._trading_gateway = trading_gateway
        self._trade_journal = trade_journal
        self._thesis_journal = thesis_journal
        self._thesis_validator = thesis_validator
        self._event_thesis_reviewer = event_thesis_reviewer
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

    def _record_submitted_exits(self, positions: list[dict[str, Any]]) -> None:
        """Record every submitted close in the trade journal and retire its thesis."""
        for position_summary in positions:
            if not isinstance(position_summary, dict) or not position_summary.get("close_submitted"):
                continue
            self._record_exit_in_trade_journal(position_summary)
            self._record_exit_in_thesis_journal(position_summary)

    def _record_exit_in_trade_journal(self, position_summary: dict[str, Any]) -> None:
        if self._trade_journal is None:
            return
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

    def _record_exit_in_thesis_journal(self, position_summary: dict[str, Any]) -> None:
        if self._thesis_journal is None:
            return
        try:
            self._thesis_journal.record_exit(position_summary)
        except Exception as exc:
            self._logger.exception(
                "Failed to close position thesis for %s: %s",
                position_summary.get("symbol"),
                exc,
            )

    def _run_thesis_validation(
        self,
        *,
        positions: list[dict[str, Any]],
        trading_client: TradingClient,
    ) -> dict[str, Any] | None:
        """Run the daily LLM thesis validator (shadow by default) after the rules layer."""
        if self._thesis_validator is None or not positions:
            return None
        try:
            return self._thesis_validator.run_due_validation(
                positions=positions,
                submit_full_exit=lambda option_symbol: self._trading_gateway.close_option_position(
                    trading_client,
                    option_symbol=option_symbol,
                ),
                submit_partial_exit=lambda option_symbol, qty: self._trading_gateway.submit_option_sell_market_order(
                    trading_client,
                    option_symbol=option_symbol,
                    qty=qty,
                ),
            )
        except Exception as exc:
            self._logger.exception("Thesis validator cycle failed: %s", exc)
            return {"error": str(exc)}

    def reconcile_outcomes(self, *, trading_client, refresh_fills=True):
        from services.trading.outcome_reconciliation import reconcile_closed_outcomes
        try:
            from services.runtime.execution_telemetry import reconcile_execution_telemetry
            from services.runtime.position_telemetry import ensure_tracked_positions
            from services.runtime.candidate_lifecycle import observe
            if refresh_fills:
                observe(reconcile_execution_telemetry, gateway=self._trading_gateway, trading_client=trading_client)
                observe(ensure_tracked_positions)
            return reconcile_closed_outcomes(gateway=self._trading_gateway, trading_client=trading_client)
        except Exception as exc:
            self._logger.exception("Outcome reconciliation failed: %s", exc)
            return {"error": str(exc)}

    def _run_event_thesis_reviews(self, *, positions: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Shadow reviews of open theses after material events; mechanical management already ran."""
        if self._event_thesis_reviewer is None or not positions:
            return None
        try:
            return self._event_thesis_reviewer.run(positions=positions)
        except Exception as exc:
            self._logger.exception("Event thesis reviews failed: %s", exc)
            return {"error": str(exc)}

    def run_cycle(self, *, trading_client: TradingClient) -> dict[str, Any]:
        from trading_support.option_positions import ManageCurrentOptionPositions

        management_result = ManageCurrentOptionPositions(
            settings=self._settings.option_positions,
            state_path=self._paths.option_position_state_path,
            execute_sales=self._settings.auto_close_option_positions,
            submit_full_exit_order=lambda option_symbol, limit_price=None: (
                self._trading_gateway.submit_option_sell_limit_order(
                    trading_client,
                    option_symbol=option_symbol,
                    limit_price=limit_price,
                )
                if limit_price is not None
                else self._trading_gateway.close_option_position(
                    trading_client,
                    option_symbol=option_symbol,
                )
            ),
            submit_partial_exit_order=lambda option_symbol, qty, limit_price=None: (
                self._trading_gateway.submit_option_sell_limit_order(
                    trading_client,
                    option_symbol=option_symbol,
                    qty=qty,
                    limit_price=limit_price,
                )
                if limit_price is not None
                else self._trading_gateway.submit_option_sell_market_order(
                    trading_client,
                    option_symbol=option_symbol,
                    qty=qty,
                )
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

        positions = (
            management_result.get("positions", [])
            if isinstance(management_result.get("positions", []), list)
            else []
        )

        from services.runtime.candidate_lifecycle import observe
        from services.runtime.execution_telemetry import reconcile_execution_telemetry
        from services.runtime.position_telemetry import ensure_tracked_positions, refresh_post_exit_returns
        from services.core.pipeline_version import build_pipeline_version
        from market_db_modules.position_outcomes import record_position_observation
        from market_db_modules.position_outcomes import position_entry_context
        management_result["execution_telemetry"] = observe(
            reconcile_execution_telemetry, gateway=self._trading_gateway, trading_client=trading_client)
        observe(ensure_tracked_positions)
        version = observe(build_pipeline_version, settings=self._settings)
        for position in positions:
            if isinstance(position, dict):
                if position.get('close_submitted'):
                    position['implied_volatility'] = observe(
                        getattr(self._trading_gateway, 'get_position_exit_iv', lambda _: None), position)
                    position['iv_observed_at'] = datetime.now(timezone.utc).isoformat()
                observe(record_position_observation, position, version=version)
                from market_db_modules.contract_research import record_observation
                observed_at = datetime.now(timezone.utc).isoformat()
                comparison = position.get('v2_exit_comparison') or {}
                observe(record_observation, 'exit:' + str(position.get('symbol')) + ':' + observed_at,
                        'POSITION_EXIT_V2', comparison, observed_at=observed_at,
                        candidate_id=(comparison.get('inputs') or {}).get('candidate_id'))
                position['candidate_context'] = observe(position_entry_context, position.get('symbol'))
                position['execution_version'] = version
        self._record_submitted_exits(positions)
        self._refresh_manager_decision_history_pnl(positions)

        thesis_validation_result = self._run_thesis_validation(
            positions=positions,
            trading_client=trading_client,
        )
        if thesis_validation_result is not None:
            management_result["thesis_validator"] = thesis_validation_result
        event_review_result = self._run_event_thesis_reviews(positions=positions)
        if event_review_result is not None:
            management_result["event_thesis_reviews"] = event_review_result

        management_result["outcome_reconciliation"] = self.reconcile_outcomes(trading_client=trading_client, refresh_fills=False)
        management_result["post_exit_returns"] = observe(refresh_post_exit_returns)

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
