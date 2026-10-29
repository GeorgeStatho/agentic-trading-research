from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from services.numbers import safe_float
from services.trading_config import WorkerSettings

from market_db_modules.portfolio_risk import (
    count_option_entry_executions_since,
    get_company_sector_key,
    get_latest_stop_loss_exit_at,
    get_realized_option_loss_since,
)
from market_db_modules.history_links import link_manager_decision_to_trade_execution
from services.trading_gateway import TradingClient
from market_db_modules.execution_telemetry import store_execution_telemetry
from market_db_modules.position_outcomes import ensure_position_outcome
from services.candidate_lifecycle import observe
from services.catalyst_gates import catalyst_admission_allowed
from services.execution_telemetry import capture_execution_context
from services.execution_telemetry import fill_metrics
from services.limit_entry import observe_entry_plan
from services.liquidity_gate import evaluate_liquidity
from services.trade_economics import recheck_trade_economics
from services.pipeline_version import build_pipeline_version, serializable


MARKET_TIMEZONE = ZoneInfo("America/New_York")


@dataclass
class TradeExecutionSession:
    """Mutable execution state used for immediate per-candidate submissions."""

    available_buying_power: float
    max_deployable_buying_power: float
    remaining_deployable_buying_power: float
    base_order_qty: int
    executions: list[dict[str, Any]] = field(default_factory=list)
    seen_option_symbols: set[str] = field(default_factory=set)
    current_option_exposure: float = 0.0
    current_option_position_count: int = 0
    sector_exposure_by_key: dict[str, float] = field(default_factory=dict)
    daily_realized_loss: float = 0.0
    open_option_symbols: set[str] = field(default_factory=set)
    # Hard dollar ceiling for one new entry's total premium; 0 disables the cap.
    max_premium_per_trade: float = 0.0
    # Buys submitted this session per underlying, for the entry attempt cap.
    entries_by_underlying: dict[str, int] = field(default_factory=dict)
    cluster_exposure_by_key: dict[str, float] = field(default_factory=dict)
    cluster_option_symbols: dict[str, set[str]] = field(default_factory=dict)


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
        settings: WorkerSettings,
        trading_gateway: Any,
        trade_journal: Any | None = None,
        thesis_journal: Any | None = None,
        follower_fleet: Any | None = None,
        logger: logging.Logger,
    ) -> None:
        self._settings = settings
        self._trading_gateway = trading_gateway
        self._trade_journal = trade_journal
        self._thesis_journal = thesis_journal
        self._follower_fleet = follower_fleet
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
        max_premium_per_trade: float = 0.0,
    ) -> tuple[int, float]:
        # Handle the calculate order qty flow in one place so callers can rely on a single, well-defined result.
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
        if max_premium_per_trade > 0.0:
            # The premium cap is a hard ceiling: it shrinks the order and, unlike
            # the sizing budget above, refuses the trade (qty 0) when even one
            # contract costs more than the allowance.
            calculated_order_qty = min(
                calculated_order_qty,
                math.floor(max_premium_per_trade / estimated_contract_cost),
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
        open_positions = self._trading_gateway.get_open_option_positions(trading_client)
        sector_exposure_by_key: dict[str, float] = {}
        cluster_exposure_by_key: dict[str, float] = {}
        cluster_option_symbols: dict[str, set[str]] = {}
        from services.risk_clusters import get_risk_cluster
        for position in open_positions:
            underlying_symbol = str(position.get("underlying_symbol") or "").strip().upper()
            sector_key = get_company_sector_key(symbol=underlying_symbol)
            sector_exposure_by_key[sector_key] = sector_exposure_by_key.get(sector_key, 0.0) + abs(
                float(position.get("notional") or 0.0)
            )
            cluster = get_risk_cluster(underlying_symbol)
            if cluster:
                cluster_exposure_by_key[cluster] = cluster_exposure_by_key.get(cluster, 0.0) + abs(float(position.get('notional') or 0))
                cluster_option_symbols.setdefault(cluster, set()).add(str(position.get('option_symbol')))
        current_option_exposure = sum(abs(float(position.get("notional") or 0.0)) for position in open_positions)
        market_now = datetime.now(MARKET_TIMEZONE)
        daily_loss_start = market_now.replace(hour=0, minute=0, second=0, microsecond=0)
        resolve_premium_cap = getattr(self._settings, "resolve_max_premium_per_trade", None)
        max_premium_per_trade = (
            float(resolve_premium_cap(available_buying_power)) if callable(resolve_premium_cap) else 0.0
        )
        return TradeExecutionSession(
            available_buying_power=available_buying_power,
            max_deployable_buying_power=max_deployable_buying_power,
            remaining_deployable_buying_power=max(0.0, max_deployable_buying_power - current_option_exposure),
            base_order_qty=base_order_qty,
            current_option_exposure=current_option_exposure,
            current_option_position_count=len(open_positions),
            sector_exposure_by_key=sector_exposure_by_key,
            daily_realized_loss=get_realized_option_loss_since(start_at=daily_loss_start),
            open_option_symbols={str(position.get("option_symbol") or "").strip().upper() for position in open_positions},
            max_premium_per_trade=max_premium_per_trade,
            cluster_exposure_by_key=cluster_exposure_by_key,
            cluster_option_symbols=cluster_option_symbols,
        )

    def _build_trade_result(self, session: TradeExecutionSession) -> dict[str, Any]:
        return {
            "ran_at": datetime.now().isoformat(),
            "paper": self._settings.alpaca_paper,
            "order_qty": session.base_order_qty,
            "available_buying_power": session.available_buying_power,
            "max_deployable_buying_power": session.max_deployable_buying_power,
            "remaining_deployable_buying_power": session.remaining_deployable_buying_power,
            "current_option_exposure": session.current_option_exposure,
            "current_option_position_count": session.current_option_position_count,
            "sector_exposure_by_key": session.sector_exposure_by_key,
            "cluster_exposure_by_key": session.cluster_exposure_by_key,
            "cluster_option_symbols": {key: sorted(value) for key, value in session.cluster_option_symbols.items()},
            "daily_realized_loss": session.daily_realized_loss,
            "portfolio_limits": {
                "max_concurrent_option_positions": int(getattr(self._settings, "max_concurrent_option_positions", 5)),
                "max_option_sector_exposure_pct": float(getattr(self._settings, "max_option_sector_exposure_pct", 50.0)),
                "daily_option_loss_limit_dollars": float(getattr(self._settings, "daily_option_loss_limit_dollars", 2000.0)),
                "max_premium_per_trade_dollars": float(session.max_premium_per_trade),
                "max_symbol_entries_per_window": int(getattr(self._settings, "max_symbol_entries_per_window", 0) or 0),
                "symbol_entry_window_days": int(getattr(self._settings, "symbol_entry_window_days", 7) or 7),
                "symbol_stop_loss_cooldown_days": int(getattr(self._settings, "symbol_stop_loss_cooldown_days", 0) or 0),
            },
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
        portfolio_guardrail: str = "",
    ) -> dict[str, Any]:
        # Assemble the execution so callers can work from one normalized shape.
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
            "portfolio_guardrail": portfolio_guardrail,
        }

    def _candidate_sector_key(self, candidate: dict[str, Any]) -> str:
        configured_sector = str(candidate.get("sector_key") or "").strip().lower()
        if configured_sector:
            return configured_sector
        return get_company_sector_key(
            symbol=str(candidate.get("symbol") or "").strip().upper(),
            company_id=candidate.get("company_id"),
        )

    @staticmethod
    def _candidate_underlying_symbol(candidate: dict[str, Any], option_symbol: str = "") -> str:
        underlying_symbol = str(candidate.get("symbol") or "").strip().upper()
        if underlying_symbol:
            return underlying_symbol
        occ_match = re.match(r"^([A-Z0-9]{1,8}?)\d{6}[CP]\d{8}$", str(option_symbol or "").strip().upper())
        return occ_match.group(1) if occ_match else ""

    def _symbol_entry_attempt_guardrail(
        self,
        *,
        underlying_symbol: str,
        session: TradeExecutionSession,
        diagnostics: dict[str, Any] | None = None,
    ) -> str:
        """Deterministic per-underlying churn brakes, from the live trade review:
        repeated re-entries into the same name after stops were a net loser, so
        cap attempts per rolling window and cool down after a stop-loss exit."""
        if not underlying_symbol:
            return ""
        current_time = datetime.now(timezone.utc)
        diagnostics = diagnostics if diagnostics is not None else {}
        diagnostics['checked_at'] = current_time.isoformat()

        entry_cap = int(getattr(self._settings, "max_symbol_entries_per_window", 0) or 0)
        if entry_cap > 0:
            window_days = max(1, int(getattr(self._settings, "symbol_entry_window_days", 7) or 7))
            try:
                recent_entry_count = count_option_entry_executions_since(
                    underlying_symbol=underlying_symbol,
                    start_at=current_time - timedelta(days=window_days),
                )
            except Exception as exc:
                self._logger.exception(
                    "Failed to count recent option entries for %s; skipping the attempt cap this cycle: %s",
                    underlying_symbol,
                    exc,
                )
                recent_entry_count = 0
            # The journal already records this session's submissions, so take the
            # max of both counters instead of summing to avoid double counting
            # while still covering a failed journal write.
            session_entry_count = session.entries_by_underlying.get(underlying_symbol, 0)
            diagnostics.update(recent_entry_count=recent_entry_count, session_entry_count=session_entry_count,
                               window_days=window_days, entry_cap=entry_cap)
            if max(recent_entry_count, session_entry_count) >= entry_cap:
                return "symbol_entry_attempt_cap"

        cooldown_days = int(getattr(self._settings, "symbol_stop_loss_cooldown_days", 0) or 0)
        if cooldown_days > 0:
            try:
                last_stop_exit_at = get_latest_stop_loss_exit_at(underlying_symbol=underlying_symbol)
            except Exception as exc:
                self._logger.exception(
                    "Failed to read the last stop-loss exit for %s; skipping the cooldown this cycle: %s",
                    underlying_symbol,
                    exc,
                )
                last_stop_exit_at = None
            diagnostics.update(last_stop_exit_at=last_stop_exit_at, cooldown_days=cooldown_days)
            if last_stop_exit_at:
                cooldown_start = (current_time - timedelta(days=cooldown_days)).strftime("%Y-%m-%d %H:%M:%S")
                if str(last_stop_exit_at) >= cooldown_start:
                    return "symbol_stop_loss_cooldown"
        return ""

    def _portfolio_guardrail_reason(
        self,
        *,
        option_symbol: str,
        underlying_symbol: str,
        estimated_order_cost: float,
        sector_key: str,
        session: TradeExecutionSession,
        diagnostics: dict[str, Any] | None = None,
    ) -> str:
        daily_loss_limit = max(1.0, float(getattr(self._settings, "daily_option_loss_limit_dollars", 2000.0)))
        if session.daily_realized_loss >= daily_loss_limit:
            return "portfolio_daily_loss_limit"
        is_existing_position = option_symbol in session.open_option_symbols
        max_positions = max(1, int(getattr(self._settings, "max_concurrent_option_positions", 5)))
        if not is_existing_position and session.current_option_position_count >= max_positions:
            return "portfolio_max_concurrent_positions"
        if session.current_option_exposure + estimated_order_cost > session.max_deployable_buying_power:
            return "portfolio_total_entry_notional_limit"
        sector_cap_pct = min(100.0, max(0.01, float(getattr(self._settings, "max_option_sector_exposure_pct", 50.0))))
        sector_cap = session.max_deployable_buying_power * (sector_cap_pct / 100.0)
        if session.sector_exposure_by_key.get(sector_key, 0.0) + estimated_order_cost > sector_cap:
            return "portfolio_sector_notional_limit"
        from services.risk_clusters import evaluate_cluster, get_risk_cluster
        cluster = evaluate_cluster(cluster=get_risk_cluster(underlying_symbol), option_symbol=option_symbol,
                                   estimated_cost=estimated_order_cost, session=session)
        if diagnostics is not None:
            diagnostics['risk_cluster_gate'] = cluster
        if not cluster['allowed']:
            return 'portfolio_risk_cluster_limit'
        return self._symbol_entry_attempt_guardrail(
            underlying_symbol=underlying_symbol,
            session=session,
            diagnostics=diagnostics,
        )

    def finalize_session(self, session: TradeExecutionSession) -> dict[str, Any]:
        """Expose the current execution-session snapshot as a trade result payload."""
        return self._build_trade_result(session)

    def execute_candidate(self, *, trading_client, candidate, session):
        from services.candidate_lifecycle import checkpoint, reject, observe
        candidate = dict(candidate)
        candidate["execution_version"] = observe(build_pipeline_version, settings=self._settings)
        from services.research_promotion import champion_check
        context = candidate.get("candidate_context")
        allowed, champion_reason, differing = champion_check(candidate)
        if not allowed:
            # A refused champion check stops every entry, so it must be as visible as a fill.
            self._logger.error("Champion guard refused %s (%s); differing manifest paths: %s",
                               candidate.get("selected_option_symbol"), champion_reason, differing)
            execution = self._build_execution(
                candidate=candidate, session=session, order_qty=0, submitted=False, order=None,
                estimated_order_cost=None, portfolio_guardrail="research_champion_only",
                error=f"Champion guard refused entry: {champion_reason}.")
            execution["champion_guard"] = {"reason": champion_reason, "differing_manifest_paths": differing}
            session.executions.append(execution)
            reject(context, "EXECUTION", "research_champion_only",
                   metrics=execution["champion_guard"], thresholds={})
            return execution
        checkpoint(context, "pre_execution", {"candidate": candidate, "session": serializable(session)})
        result = self._execute_candidate(trading_client=trading_client, candidate=candidate, session=session)
        if result.get("submitted"):
            checkpoint(context, "order_submitted", result)
        else:
            reason = result.get("portfolio_guardrail") or "execution_not_submitted"
            reject(context, "EXECUTION", reason, metrics=result,
                   thresholds=(candidate.get("execution_version") or {}).get("runtime_settings", {}))
        return result

    def _execute_candidate(
        self,
        *,
        trading_client: TradingClient,
        candidate: dict[str, Any],
        session: TradeExecutionSession,
    ) -> dict[str, Any]:
        """Execute one candidate immediately and update the shared session state."""
        if not catalyst_admission_allowed(candidate.get('candidate_context')):
            execution = self._build_execution(candidate=candidate, session=session, order_qty=0,
                submitted=False, order=None, estimated_order_cost=None, error='Catalyst admission blocked or unavailable.',
                portfolio_guardrail='catalyst_admission_blocked')
            session.executions.append(execution)
            return execution
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
            max_premium_per_trade=session.max_premium_per_trade,
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

        if calculated_order_qty <= 0:
            # A single contract already costs more than the per-trade premium
            # ceiling, so there is no size at which this entry is allowed.
            self._logger.info(
                "Skipping %s because one contract costs %.2f, above the %.2f per-trade premium cap.",
                option_symbol,
                estimated_contract_cost,
                session.max_premium_per_trade,
            )
            execution = self._build_execution(
                candidate=candidate,
                session=session,
                order_qty=0,
                submitted=False,
                order=None,
                estimated_order_cost=estimated_contract_cost,
                error=(
                    f"Skipped because one contract cost {estimated_contract_cost:.2f} exceeds the "
                    f"{session.max_premium_per_trade:.2f} per-trade premium cap."
                ),
                portfolio_guardrail="premium_per_trade_cap",
            )
            session.executions.append(execution)
            return execution

        estimated_order_cost = calculated_order_qty * estimated_contract_cost
        sector_key = self._candidate_sector_key(candidate)
        candidate['portfolio_gate_inputs'] = {}
        portfolio_guardrail = self._portfolio_guardrail_reason(
            option_symbol=option_symbol,
            underlying_symbol=self._candidate_underlying_symbol(candidate, option_symbol),
            estimated_order_cost=estimated_order_cost,
            sector_key=sector_key,
            session=session,
            diagnostics=candidate['portfolio_gate_inputs'],
        )
        from services.contract_observations import record_contract_observation
        from services.candidate_lifecycle import reject
        cluster_gate = candidate['portfolio_gate_inputs'].get('risk_cluster_gate')
        if cluster_gate:
            candidate['risk_cluster_id'] = cluster_gate['risk_cluster_id']
            record_contract_observation(candidate.get('candidate_context'), 'RISK_CLUSTER', cluster_gate)
            if cluster_gate['would_allow'] is False and cluster_gate['enforcement_mode'] != 'disabled':
                reject(candidate.get('candidate_context'), 'RISK_CLUSTER', 'portfolio_risk_cluster_limit',
                       metrics=cluster_gate['metrics'], thresholds=cluster_gate['thresholds'], shadow=cluster_gate['allowed'])
        if portfolio_guardrail:
            execution = self._build_execution(
                candidate=candidate,
                session=session,
                order_qty=calculated_order_qty,
                submitted=False,
                order=None,
                estimated_order_cost=estimated_order_cost,
                error=f"Skipped because {portfolio_guardrail} was reached.",
                portfolio_guardrail=portfolio_guardrail,
            )
            session.executions.append(execution)
            return execution

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

        from services.candidate_lifecycle import checkpoint, observe
        arrival = observe(getattr(self._trading_gateway, 'get_execution_market_snapshot', lambda **_: None),
                          option_symbol=option_symbol, underlying_symbol=candidate.get("symbol"))
        candidate["execution_telemetry"] = capture_execution_context(candidate, arrival)
        from services.candidate_lifecycle import reject
        liquidity = evaluate_liquidity({'latest_quote': (arrival or {}).get('option') or {}}, quantity=calculated_order_qty)
        candidate['execution_liquidity_gate'] = liquidity
        record_contract_observation(candidate.get('candidate_context'), 'EXECUTION_LIQUIDITY', liquidity)
        for reason in liquidity['reason_codes'] if liquidity['enforcement_mode'] != 'disabled' else []:
            reject(candidate.get('candidate_context'), 'EXECUTION_LIQUIDITY', reason,
                   metrics=liquidity['metrics'], thresholds=liquidity['thresholds'], shadow=liquidity['allowed'])
        if not liquidity['allowed']:
            execution = self._build_execution(candidate=candidate, session=session, order_qty=calculated_order_qty,
                submitted=False, order=None, estimated_order_cost=estimated_order_cost,
                error='Fresh quote failed V2 liquidity checks.', portfolio_guardrail='execution_liquidity_blocked')
            session.executions.append(execution)
            return execution
        economics = recheck_trade_economics(candidate, arrival)
        if economics is not None:
            candidate['trade_economics_recheck'] = economics
            checkpoint(candidate.get('candidate_context'), 'TRADE_ECONOMICS_SUBMISSION', economics)
            for reason in economics['reason_codes']:
                reject(candidate.get('candidate_context'), 'TRADE_ECONOMICS_SUBMISSION', reason,
                       metrics=economics['metrics'], shadow=economics['allowed'])
            if not economics['allowed']:
                execution = self._build_execution(candidate=candidate, session=session, order_qty=calculated_order_qty,
                    submitted=False, order=None, estimated_order_cost=estimated_order_cost,
                    error='Trade-economics report failed its submission recheck.',
                    portfolio_guardrail='economics_recheck_failed')
                session.executions.append(execution)
                return execution
        candidate['limit_entry_shadow'] = observe(observe_entry_plan, candidate, arrival,
                                                  quantity=calculated_order_qty, paper=self._settings.alpaca_paper)
        record_contract_observation(candidate.get('candidate_context'), 'LIMIT_ENTRY_SHADOW', candidate['limit_entry_shadow'])
        checkpoint(candidate.get("candidate_context"), "pre_order", {
            "candidate": candidate, "quantity": calculated_order_qty,
            "estimated_order_cost": estimated_order_cost, "session": self._build_trade_result(session),
        })
        try:
            order_summary = self._trading_gateway.submit_option_market_order(
                trading_client,
                option_symbol=option_symbol,
                qty=calculated_order_qty,
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
        else:
            self._reserve_session_capacity(
                session=session,
                option_symbol=option_symbol,
                underlying_symbol=self._candidate_underlying_symbol(candidate, option_symbol),
                sector_key=sector_key,
                estimated_order_cost=estimated_order_cost,
                risk_cluster_id=candidate.get('risk_cluster_id'),
            )
            execution = self._build_execution(
                candidate=candidate,
                session=session,
                order_qty=calculated_order_qty,
                submitted=True,
                order=order_summary,
                estimated_order_cost=estimated_order_cost,
                error="",
            )
            self._record_trade_journal_entry(execution, option_symbol=option_symbol)
            self._link_manager_decision_history(
                execution,
                candidate=candidate,
                order_summary=order_summary,
                option_symbol=option_symbol,
            )
            self._record_thesis_entry(execution, option_symbol=option_symbol)
            observe(self._observe_submitted_order, trading_client, execution)
            if self._follower_fleet is not None:
                # Followers mirror only what production submitted, and can never fail it.
                observe(self._follower_fleet.mirror_entry, candidate, execution)


        session.executions.append(execution)
        return execution

    def _observe_submitted_order(self, trading_client, execution):
        order_id = str(execution["order"]["id"])
        order = self._trading_gateway.get_order_status_summary(trading_client, order_id=order_id)
        if not isinstance(order, dict):
            return
        market = observe(getattr(self._trading_gateway, 'get_execution_market_snapshot', lambda **_: None),
            option_symbol=execution["selected_option_symbol"], underlying_symbol=execution.get("symbol"))
        metrics = fill_metrics(order, execution.get("execution_telemetry") or {}, market_snapshot=market)
        execution.setdefault("execution_telemetry", {}).update(metrics)
        store_execution_telemetry(order_id, metrics)
        ensure_position_outcome(order_id)

    @staticmethod
    def _reserve_session_capacity(
        *,
        session: TradeExecutionSession,
        option_symbol: str,
        sector_key: str,
        estimated_order_cost: float,
        underlying_symbol: str = "",
        risk_cluster_id: str | None = None,
    ) -> None:
        """Consume shared session budgets once an order has been submitted."""
        session.remaining_deployable_buying_power -= estimated_order_cost
        session.current_option_exposure += estimated_order_cost
        session.sector_exposure_by_key[sector_key] = (
            session.sector_exposure_by_key.get(sector_key, 0.0) + estimated_order_cost
        )
        if underlying_symbol:
            session.entries_by_underlying[underlying_symbol] = (
                session.entries_by_underlying.get(underlying_symbol, 0) + 1
            )
        if risk_cluster_id:
            session.cluster_exposure_by_key[risk_cluster_id] = session.cluster_exposure_by_key.get(risk_cluster_id, 0.0) + estimated_order_cost
            session.cluster_option_symbols.setdefault(risk_cluster_id, set()).add(option_symbol)
        if option_symbol not in session.open_option_symbols:
            session.open_option_symbols.add(option_symbol)
            session.current_option_position_count += 1

    def _record_trade_journal_entry(self, execution: dict[str, Any], *, option_symbol: str) -> None:
        if self._trade_journal is None:
            return
        try:
            record_id = self._trade_journal.record_execution(execution)
            execution["db_recorded"] = record_id is not None
            execution["db_record_id"] = record_id
        except Exception as exc:
            self._logger.exception(
                "Failed to record executed option trade in DB for %s: %s",
                option_symbol,
                exc,
            )
            execution["db_recorded"] = False
            execution["db_record_error"] = str(exc)

    def _link_manager_decision_history(
        self,
        execution: dict[str, Any],
        *,
        candidate: dict[str, Any],
        order_summary: dict[str, Any],
        option_symbol: str,
    ) -> None:
        try:
            manager_decision_history_id = link_manager_decision_to_trade_execution(
                trade_execution_order_id=str(order_summary.get("id") or ""),
                trade_execution_record_id=execution.get("db_record_id"),
                company_id=candidate.get("company_id"),
                symbol=candidate.get("symbol"),
                selected_option_symbol=option_symbol,
                submitted_at=order_summary.get("submitted_at"),
            )
            execution["manager_decision_history_linked"] = manager_decision_history_id is not None
            execution["manager_decision_history_id"] = manager_decision_history_id
        except Exception as exc:
            self._logger.exception(
                "Failed to link option execution back to manager decision history for %s: %s",
                option_symbol,
                exc,
            )
            execution["manager_decision_history_linked"] = False
            execution["manager_decision_history_link_error"] = str(exc)

    def _record_thesis_entry(self, execution: dict[str, Any], *, option_symbol: str) -> None:
        # Phase 0 of the thesis validator: capture the entry thesis now, because
        # it cannot be backfilled later. Never blocks the trade.
        if self._thesis_journal is None:
            return
        try:
            thesis_id = self._thesis_journal.record_entry(execution)
            execution["thesis_recorded"] = thesis_id is not None
            execution["thesis_id"] = thesis_id
        except Exception as exc:
            self._logger.exception(
                "Failed to record position thesis for %s: %s",
                option_symbol,
                exc,
            )
            execution["thesis_recorded"] = False
            execution["thesis_record_error"] = str(exc)

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
