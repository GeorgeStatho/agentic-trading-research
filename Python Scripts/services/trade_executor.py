from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from services.common import safe_float
from services.config import FrontMainSettings
from services.trading_gateway import TradingClient


@dataclass
class TradeExecutionSession:
    """Mutable execution state used for immediate per-candidate submissions."""

    available_buying_power: float
    max_deployable_buying_power: float
    remaining_deployable_buying_power: float
    base_order_qty: int
    executions: list[dict[str, Any]] = field(default_factory=list)
    seen_option_symbols: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class OptionExposureSnapshot:
    """Current open-option exposure versus the configured buying-power cap."""

    available_buying_power: float
    max_deployable_buying_power: float
    current_option_exposure: float
    option_position_count: int

    @property
    def remaining_deployable_buying_power(self) -> float:
        return max(0.0, self.max_deployable_buying_power - self.current_option_exposure)

    @property
    def exceeds_max_exposure(self) -> bool:
        return self.current_option_exposure >= self.max_deployable_buying_power


class OptionTradeExecutor:
    """Size and submit option orders using portfolio buying-power rules."""

    def __init__(
        self,
        *,
        settings: FrontMainSettings,
        trading_gateway: Any,
        logger: logging.Logger,
    ) -> None:
        self._settings = settings
        self._trading_gateway = trading_gateway
        self._logger = logger

    def _get_option_reference_price(self, selected_option: dict[str, Any]) -> float:
        latest_quote = selected_option.get("latest_quote", {})
        if isinstance(latest_quote, dict):
            for key in ("ask_price", "midpoint_price", "bid_price"):
                price = safe_float(latest_quote.get(key))
                if price is not None and price > 0:
                    return price

        for key in ("latest_trade_price", "close_price"):
            price = safe_float(selected_option.get(key))
            if price is not None and price > 0:
                return price

        return 0.0

    def _calculate_order_qty(
        self,
        *,
        option_reference_price: float,
        base_order_qty: int,
        max_deployable_buying_power: float,
    ) -> tuple[int, float]:
        if option_reference_price <= 0.0:
            return 0, 0.0

        estimated_contract_cost = option_reference_price * self._settings.option_contract_multiplier
        calculated_order_qty = max(
            base_order_qty,
            min(
                math.floor(
                    (max_deployable_buying_power * self._settings.per_order_sizing_buying_power_ratio)
                    / estimated_contract_cost
                ),
                base_order_qty * self._settings.max_option_order_qty_multiplier,
            ),
        )
        return calculated_order_qty, estimated_contract_cost

    def create_session(
        self,
        *,
        trading_client: TradingClient,
        order_qty: int | None = None,
    ) -> TradeExecutionSession:
        """Create a trade execution session for immediate submissions."""
        base_order_qty = max(1, int(order_qty or self._settings.default_option_order_qty))
        available_buying_power = self._trading_gateway.get_available_buying_power(trading_client)
        max_deployable_buying_power = available_buying_power * self._settings.max_deployable_buying_power_ratio
        return TradeExecutionSession(
            available_buying_power=available_buying_power,
            max_deployable_buying_power=max_deployable_buying_power,
            remaining_deployable_buying_power=max_deployable_buying_power,
            base_order_qty=base_order_qty,
        )

    def _build_trade_result(self, session: TradeExecutionSession) -> dict[str, Any]:
        return {
            "ran_at": datetime.now().isoformat(),
            "paper": self._settings.alpaca_paper,
            "order_qty": session.base_order_qty,
            "available_buying_power": session.available_buying_power,
            "max_deployable_buying_power": session.max_deployable_buying_power,
            "remaining_deployable_buying_power": session.remaining_deployable_buying_power,
            "submitted_count": sum(1 for execution in session.executions if execution.get("submitted")),
            "candidate_count": len(session.executions),
            "executions": session.executions,
        }

    def _build_execution(
        self,
        *,
        candidate: dict[str, Any],
        session: TradeExecutionSession,
        order_qty: int,
        submitted: bool,
        order: dict[str, Any] | None,
        estimated_order_cost: float | None,
        error: str,
    ) -> dict[str, Any]:
        return {
            **candidate,
            "order_qty": order_qty,
            "submitted": submitted,
            "order": order,
            "estimated_order_cost": estimated_order_cost,
            "available_buying_power": session.available_buying_power,
            "max_deployable_buying_power": session.max_deployable_buying_power,
            "remaining_deployable_buying_power": session.remaining_deployable_buying_power,
            "error": error,
        }

    def finalize_session(self, session: TradeExecutionSession) -> dict[str, Any]:
        """Expose the current execution-session snapshot as a trade result payload."""
        return self._build_trade_result(session)

    def execute_candidate(
        self,
        *,
        trading_client: TradingClient,
        candidate: dict[str, Any],
        session: TradeExecutionSession,
    ) -> dict[str, Any]:
        """Execute one candidate immediately and update the shared session state."""
        option_symbol = str(candidate.get("selected_option_symbol") or "").strip().upper()
        if not option_symbol:
            execution = self._build_execution(
                candidate=candidate,
                session=session,
                order_qty=session.base_order_qty,
                submitted=False,
                order=None,
                estimated_order_cost=None,
                error="Selected option symbol was missing.",
            )
            session.executions.append(execution)
            return execution

        if option_symbol in session.seen_option_symbols:
            execution = self._build_execution(
                candidate=candidate,
                session=session,
                order_qty=session.base_order_qty,
                submitted=False,
                order=None,
                estimated_order_cost=None,
                error="Skipped duplicate option symbol within the current execution cycle.",
            )
            session.executions.append(execution)
            return execution

        session.seen_option_symbols.add(option_symbol)

        selected_option = candidate.get("selected_option") or {}
        option_reference_price = self._get_option_reference_price(selected_option)
        calculated_order_qty, estimated_contract_cost = self._calculate_order_qty(
            option_reference_price=option_reference_price,
            base_order_qty=session.base_order_qty,
            max_deployable_buying_power=session.max_deployable_buying_power,
        )

        if estimated_contract_cost == 0.0:
            self._logger.info(
                "Skipping %s because no usable option price was available to estimate order cost.",
                option_symbol,
            )
            execution = self._build_execution(
                candidate=candidate,
                session=session,
                order_qty=session.base_order_qty,
                submitted=False,
                order=None,
                estimated_order_cost=None,
                error="No usable option price was available to estimate order cost.",
            )
            session.executions.append(execution)
            return execution

        estimated_order_cost = calculated_order_qty * estimated_contract_cost
        if estimated_order_cost > session.remaining_deployable_buying_power:
            self._logger.info(
                "Skipping %s because estimated cost %.2f exceeds remaining deployable buying power %.2f (%.2f%% of account buying power).",
                option_symbol,
                estimated_order_cost,
                session.remaining_deployable_buying_power,
                self._settings.max_deployable_buying_power_pct,
            )
            execution = self._build_execution(
                candidate=candidate,
                session=session,
                order_qty=calculated_order_qty,
                submitted=False,
                order=None,
                estimated_order_cost=estimated_order_cost,
                error=(
                    f"Estimated order cost exceeded {self._settings.max_deployable_buying_power_pct:.2f}% "
                    "buying power allowance."
                ),
            )
            session.executions.append(execution)
            return execution

        self._logger.info(
            "Submitting BUY market order for %s via %s (company=%s, decision=%s, confidence=%s, estimated_cost=%.2f, remaining_buying_power_cap=%.2f, cap_pct=%.2f)",
            option_symbol,
            candidate.get("selected_option_source"),
            candidate.get("symbol"),
            candidate.get("decision"),
            candidate.get("confidence"),
            estimated_order_cost,
            session.remaining_deployable_buying_power,
            self._settings.max_deployable_buying_power_pct,
        )

        try:
            order_summary = self._trading_gateway.submit_option_market_order(
                trading_client,
                option_symbol=option_symbol,
                qty=calculated_order_qty,
            )
            session.remaining_deployable_buying_power -= estimated_order_cost
            execution = self._build_execution(
                candidate=candidate,
                session=session,
                order_qty=calculated_order_qty,
                submitted=True,
                order=order_summary,
                estimated_order_cost=estimated_order_cost,
                error="",
            )
        except Exception as exc:
            self._logger.exception("Failed to submit option order for %s: %s", option_symbol, exc)
            execution = self._build_execution(
                candidate=candidate,
                session=session,
                order_qty=calculated_order_qty,
                submitted=False,
                order=None,
                estimated_order_cost=estimated_order_cost,
                error=str(exc),
            )

        session.executions.append(execution)
        return execution

    def execute(
        self,
        *,
        trading_client: TradingClient,
        order_candidates: list[dict[str, Any]],
        order_qty: int | None = None,
    ) -> dict[str, Any]:
        session = self.create_session(trading_client=trading_client, order_qty=order_qty)
        for candidate in order_candidates:
            self.execute_candidate(
                trading_client=trading_client,
                candidate=candidate,
                session=session,
            )
        return self.finalize_session(session)
