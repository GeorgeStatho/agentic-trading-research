from __future__ import annotations

"""Front-facing trading entrypoint.

This module now follows a service-oriented layout:
- configuration loading is isolated in dataclasses
- status persistence is handled by a dedicated reporter
- cold-start database validation is handled by a dedicated checker
- Alpaca communication lives behind a trading gateway
- option order sizing/submission is handled by a trade executor
- the outer loops only coordinate scheduled tasks

The public compatibility entrypoints remain:
- ``main(...)``
- ``main_loop()``
- ``option_manager_loop()``
"""

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import sys
import time
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    ALPACA_IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - optional dependency
    TradingClient = Any
    OrderSide = None
    TimeInForce = None
    MarketOrderRequest = None
    ALPACA_IMPORT_ERROR = exc


PYTHON_SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT_DIR = PYTHON_SCRIPTS_DIR.parent
DATA_DIR = ROOT_DIR / "Data"
LOGS_DIR = DATA_DIR / "logs"
ENV_PATH = ROOT_DIR / ".env"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"

for path in (PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.append(normalized)

if load_dotenv is not None:
    load_dotenv(ENV_PATH)

from agentCallers.main import run_full_agent_stack
from db_helpers import (
    ensure_all_sector_market_data,
    get_all_companies,
    get_all_industries,
    get_all_sectors,
    initialize_market_database,
    initialize_news_database,
)


LOGGER = logging.getLogger("front_main")


def _env_flag(name: str, default: bool) -> bool:
    """Read a boolean-like environment variable."""
    value = str(os.getenv(name, str(default))).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _env_percentage(name: str, default: float) -> float:
    """Read a 0..100 percentage environment variable."""
    raw_value = str(os.getenv(name, str(default))).strip()
    try:
        parsed = float(raw_value)
    except ValueError:
        parsed = float(default)
    return min(100.0, max(0.0, parsed))


def _env_positive_int(name: str, default: int) -> int:
    """Read a positive integer environment variable."""
    raw_value = str(os.getenv(name, str(default))).strip()
    try:
        parsed = int(raw_value)
    except ValueError:
        parsed = int(default)
    return max(1, parsed)


def _safe_float(value: Any) -> float | None:
    """Convert a value to float when possible."""
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_exit_hours_to_expiration() -> float:
    """Preserve the existing env fallback from hours to days."""
    raw_value = str(
        os.getenv(
            "OPTION_POSITION_EXIT_HOURS_TO_EXPIRATION",
            os.getenv("OPTION_POSITION_EXIT_DAYS_TO_EXPIRATION", "1"),
        )
    ).strip()
    hours_value = float(raw_value)
    multiplier = 1.0 if os.getenv("OPTION_POSITION_EXIT_HOURS_TO_EXPIRATION") is not None else 24.0
    return max(0.0, hours_value * multiplier)


@dataclass(frozen=True)
class FrontMainPaths:
    """Filesystem paths used by the front-facing trading workflow."""

    root_dir: Path
    data_dir: Path
    logs_dir: Path
    status_path: Path
    trade_output_path: Path
    option_position_management_output_path: Path
    agent_output_path: Path
    selected_options_output_path: Path
    combined_output_path: Path

    @classmethod
    def from_env(cls) -> FrontMainPaths:
        return cls(
            root_dir=ROOT_DIR,
            data_dir=DATA_DIR,
            logs_dir=LOGS_DIR,
            status_path=Path(
                os.getenv("SCRIPT_STATUS_PATH", str(ROOT_DIR / "web_dashboard" / "public" / "script_status.json"))
            ),
            trade_output_path=Path(
                os.getenv(
                    "TRADE_EXECUTION_OUTPUT_PATH",
                    str(DATA_DIR / "trade_execution_output.json"),
                )
            ),
            option_position_management_output_path=Path(
                os.getenv(
                    "OPTION_POSITION_MANAGEMENT_OUTPUT_PATH",
                    str(DATA_DIR / "option_position_management_output.json"),
                )
            ),
            agent_output_path=DATA_DIR / "agent_runner_output.json",
            selected_options_output_path=DATA_DIR / "selected_options_output.json",
            combined_output_path=DATA_DIR / "front_main_output.json",
        )


@dataclass(frozen=True)
class FrontMainSettings:
    """Runtime configuration for trading cycles and loop scheduling."""

    default_option_order_qty: int
    option_contract_multiplier: int
    run_interval_seconds: int
    market_recheck_seconds: int
    auto_manage_option_positions: bool
    auto_close_option_positions: bool
    option_position_management_interval_seconds: int
    option_position_take_profit_pct: float
    option_position_stop_loss_pct: float
    option_position_exit_hours_to_expiration: float
    max_deployable_buying_power_pct: float
    per_order_sizing_buying_power_pct: float
    max_option_order_qty_multiplier: int
    alpaca_paper: bool
    cold_start_sanity_check_enabled: bool

    @property
    def max_deployable_buying_power_ratio(self) -> float:
        return self.max_deployable_buying_power_pct / 100.0

    @property
    def per_order_sizing_buying_power_ratio(self) -> float:
        return self.per_order_sizing_buying_power_pct / 100.0

    @classmethod
    def from_env(cls) -> FrontMainSettings:
        market_recheck_seconds = 5 * 60
        return cls(
            default_option_order_qty=max(1, int(os.getenv("AGENT_OPTION_ORDER_QTY", "1"))),
            option_contract_multiplier=100,
            run_interval_seconds=max(60, int(os.getenv("RUN_INTERVAL_SECONDS", str(3 * 60 * 60)))),
            market_recheck_seconds=market_recheck_seconds,
            auto_manage_option_positions=_env_flag("AUTO_MANAGE_OPTION_POSITIONS", True),
            auto_close_option_positions=_env_flag("AUTO_CLOSE_OPTION_POSITIONS", True),
            option_position_management_interval_seconds=max(
                60,
                int(os.getenv("OPTION_POSITION_MANAGEMENT_INTERVAL_SECONDS", str(market_recheck_seconds))),
            ),
            option_position_take_profit_pct=float(os.getenv("OPTION_POSITION_TAKE_PROFIT_PCT", "25")),
            option_position_stop_loss_pct=float(os.getenv("OPTION_POSITION_STOP_LOSS_PCT", "-20")),
            option_position_exit_hours_to_expiration=_load_exit_hours_to_expiration(),
            max_deployable_buying_power_pct=_env_percentage("MAX_DEPLOYABLE_BUYING_POWER_PCT", 30.0),
            per_order_sizing_buying_power_pct=_env_percentage("PER_ORDER_SIZING_BUYING_POWER_PCT", 30.0),
            max_option_order_qty_multiplier=_env_positive_int("MAX_OPTION_ORDER_QTY_MULTIPLIER", 50),
            alpaca_paper=_env_flag("ALPACA_PAPER", True),
            cold_start_sanity_check_enabled=_env_flag("COLD_START_SANITY_CHECK", True),
        )


@dataclass
class TradeExecutionSession:
    """Mutable execution state used for immediate per-candidate submissions."""

    available_buying_power: float
    max_deployable_buying_power: float
    remaining_deployable_buying_power: float
    base_order_qty: int
    executions: list[dict[str, Any]] = field(default_factory=list)
    seen_option_symbols: set[str] = field(default_factory=set)


class StatusReporter:
    """Persist loop state for the dashboard and external monitors."""

    def __init__(self, status_path: Path) -> None:
        self._status_path = status_path

    def write(self, state: str, message: str, **extra: Any) -> None:
        payload = {
            "state": state,
            "message": message,
            "pid": os.getpid(),
            "updated_at": datetime.now().isoformat(),
            **extra,
        }
        self._status_path.parent.mkdir(parents=True, exist_ok=True)
        with self._status_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)


class JsonFileWriter:
    """Small utility that isolates JSON file persistence."""

    @staticmethod
    def write(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)


class ColdStartSanityChecker:
    """Validate and optionally hydrate the market/news databases."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def run(self) -> dict[str, Any]:
        initialize_market_database()
        initialize_news_database()

        sectors = get_all_sectors()
        if not sectors:
            raise RuntimeError(
                "Cold-start sanity check failed: no sectors were seeded. "
                "Verify Data/sector_etfs.json exists and is non-empty."
            )

        industries = get_all_industries()
        companies = get_all_companies()
        needs_full_market_bootstrap = not industries or not companies

        result: dict[str, Any] = {
            "sectors_seeded": len(sectors),
            "industries_present": len(industries),
            "companies_present": len(companies),
            "performed_full_market_bootstrap": needs_full_market_bootstrap,
        }

        if not needs_full_market_bootstrap:
            return result

        self._logger.info(
            "Cold-start sanity check detected an empty market DB. Bootstrapping all %s sectors from yfinance.",
            len(sectors),
        )
        hydrated_sectors = ensure_all_sector_market_data()
        if not hydrated_sectors:
            raise RuntimeError(
                "Cold-start sanity check failed: could not hydrate sectors from yfinance."
            )
        self._logger.info(
            "Cold-start full market bootstrap finished. Hydrated %s sectors from yfinance.",
            len(hydrated_sectors),
        )

        refreshed_industries = get_all_industries()
        refreshed_companies = get_all_companies()
        hydrated_sector_keys = [
            str(sector.get("sector_key") or "").strip()
            for sector in hydrated_sectors
            if str(sector.get("sector_key") or "").strip()
        ]
        result["industries_present"] = len(refreshed_industries)
        result["companies_present"] = len(refreshed_companies)
        result["hydrated_sector_count"] = len(hydrated_sector_keys)
        result["hydrated_sector_keys"] = hydrated_sector_keys

        if not refreshed_industries:
            raise RuntimeError(
                "Cold-start sanity check failed: sectors seeded but no industries were loaded from yfinance."
            )
        if not refreshed_companies:
            raise RuntimeError(
                "Cold-start sanity check failed: industries loaded but no companies were loaded from yfinance."
            )

        return result


class AlpacaTradingGateway:
    """Encapsulate Alpaca-specific trading operations."""

    def __init__(self, *, paper: bool, logger: logging.Logger) -> None:
        self._paper = paper
        self._logger = logger

    def create_client(self) -> TradingClient:
        if ALPACA_IMPORT_ERROR is not None:
            raise RuntimeError(
                "alpaca-py is required to run the trading entrypoint. "
                f"Original import error: {ALPACA_IMPORT_ERROR}"
            )

        api_key = str(os.getenv("PUBLIC_KEY") or "").strip()
        api_secret = str(os.getenv("PRIVATE_KEY") or "").strip()
        if not api_key or not api_secret:
            raise RuntimeError("PUBLIC_KEY and PRIVATE_KEY must be configured in .env")

        os.environ.pop("APCA_OAUTH_TOKEN", None)
        os.environ.pop("ALPACA_OAUTH_TOKEN", None)
        return TradingClient(api_key=api_key, secret_key=api_secret, oauth_token=None, paper=self._paper)

    def market_is_open(self, trading_client: TradingClient) -> bool:
        try:
            clock = trading_client.get_clock()
            return bool(getattr(clock, "is_open", False))
        except Exception as exc:
            self._logger.exception("Failed to check Alpaca market clock: %s", exc)
            return False

    def get_available_buying_power(self, trading_client: TradingClient) -> float:
        account = trading_client.get_account()
        buying_power = _safe_float(getattr(account, "buying_power", None))
        if buying_power is None:
            raise RuntimeError("Unable to read buying_power from Alpaca account.")
        return buying_power

    def submit_option_market_order(
        self,
        trading_client: TradingClient,
        *,
        option_symbol: str,
        qty: int,
    ) -> dict[str, Any]:
        if MarketOrderRequest is None or OrderSide is None or TimeInForce is None:
            raise RuntimeError("alpaca-py is required to submit option orders.")

        order_request = MarketOrderRequest(
            symbol=option_symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        order = trading_client.submit_order(order_data=order_request)
        return {
            "id": str(getattr(order, "id", "")),
            "symbol": str(getattr(order, "symbol", option_symbol)),
            "asset_class": str(getattr(order, "asset_class", "")),
            "qty": str(getattr(order, "qty", qty)),
            "side": str(getattr(order, "side", "")),
            "type": str(getattr(order, "type", "")),
            "time_in_force": str(getattr(order, "time_in_force", "")),
            "status": str(getattr(order, "status", "")),
            "submitted_at": str(getattr(order, "submitted_at", "")),
        }


class OrderCandidateBuilder:
    """Turn agent output into executable option-order candidates."""

    @staticmethod
    def _extract_selected_option_candidates(agent_result: dict[str, Any]) -> list[dict[str, Any]]:
        selected_options = agent_result.get("selected_options", {})
        companies = selected_options.get("companies", [])
        return companies if isinstance(companies, list) else []

    @staticmethod
    def _build_candidate(company_result: dict[str, Any]) -> dict[str, Any] | None:
        decision = str(company_result.get("decision") or "").strip().lower()
        confidence = str(company_result.get("confidence") or "").strip().lower()
        selected_option = company_result.get("selected_option") or {}
        option_symbol = str(selected_option.get("symbol") or "").strip().upper()

        if decision not in {"call", "put"}:
            return None
        if confidence != "high":
            return None
        if not option_symbol:
            return None

        return {
            "company_id": company_result.get("company_id"),
            "symbol": company_result.get("symbol"),
            "name": company_result.get("name"),
            "decision": decision,
            "confidence": confidence,
            "selected_option_id": company_result.get("selected_option_id"),
            "selected_expiration_date": company_result.get("selected_expiration_date"),
            "selected_strike_price": company_result.get("selected_strike_price"),
            "selected_option_source": company_result.get("selected_option_source"),
            "selected_option_symbol": option_symbol,
            "selected_option": selected_option,
            "reason": company_result.get("reason"),
        }

    def build(self, agent_result: dict[str, Any]) -> list[dict[str, Any]]:
        return self.build_from_company_results(self._extract_selected_option_candidates(agent_result))

    def build_from_company_results(self, company_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build unique order candidates from selected-option company summaries."""
        candidates: list[dict[str, Any]] = []
        seen_option_symbols: set[str] = set()

        for company_result in company_results:
            candidate = self._build_candidate(company_result)
            if candidate is None:
                continue

            option_symbol = str(candidate.get("selected_option_symbol") or "").strip().upper()
            if option_symbol in seen_option_symbols:
                continue

            seen_option_symbols.add(option_symbol)
            candidates.append(candidate)

        return candidates

    def build_from_manager_result(self, manager_result: dict[str, Any]) -> dict[str, Any] | None:
        """Build a single executable candidate from one manager result."""
        company = dict(manager_result.get("company") or {})
        recommendation = dict(manager_result.get("recommendation") or {})
        company_result = {
            "company_id": company.get("company_id"),
            "symbol": company.get("symbol"),
            "name": company.get("name"),
            "decision": recommendation.get("decision"),
            "confidence": recommendation.get("confidence"),
            "selected_option_id": recommendation.get("selected_option_id"),
            "selected_expiration_date": recommendation.get("selected_expiration_date"),
            "selected_strike_price": recommendation.get("selected_strike_price"),
            "selected_option_source": recommendation.get("selected_option_source"),
            "selected_option": manager_result.get("selected_option"),
            "reason": recommendation.get("reason"),
        }
        return self._build_candidate(company_result)


class OptionTradeExecutor:
    """Size and submit option orders using portfolio buying-power rules."""

    def __init__(
        self,
        *,
        settings: FrontMainSettings,
        trading_gateway: AlpacaTradingGateway,
        logger: logging.Logger,
    ) -> None:
        self._settings = settings
        self._trading_gateway = trading_gateway
        self._logger = logger

    def _get_option_reference_price(self, selected_option: dict[str, Any]) -> float:
        latest_quote = selected_option.get("latest_quote", {})
        if isinstance(latest_quote, dict):
            for key in ("ask_price", "midpoint_price", "bid_price"):
                price = _safe_float(latest_quote.get(key))
                if price is not None and price > 0:
                    return price

        for key in ("latest_trade_price", "close_price"):
            price = _safe_float(selected_option.get(key))
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
            execution = {
                **candidate,
                "order_qty": session.base_order_qty,
                "submitted": False,
                "order": None,
                "estimated_order_cost": None,
                "available_buying_power": session.available_buying_power,
                "max_deployable_buying_power": session.max_deployable_buying_power,
                "remaining_deployable_buying_power": session.remaining_deployable_buying_power,
                "error": "Selected option symbol was missing.",
            }
            session.executions.append(execution)
            return execution

        if option_symbol in session.seen_option_symbols:
            execution = {
                **candidate,
                "order_qty": session.base_order_qty,
                "submitted": False,
                "order": None,
                "estimated_order_cost": None,
                "available_buying_power": session.available_buying_power,
                "max_deployable_buying_power": session.max_deployable_buying_power,
                "remaining_deployable_buying_power": session.remaining_deployable_buying_power,
                "error": "Skipped duplicate option symbol within the current execution cycle.",
            }
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
            execution = {
                **candidate,
                "order_qty": session.base_order_qty,
                "submitted": False,
                "order": None,
                "estimated_order_cost": None,
                "available_buying_power": session.available_buying_power,
                "max_deployable_buying_power": session.max_deployable_buying_power,
                "remaining_deployable_buying_power": session.remaining_deployable_buying_power,
                "error": "No usable option price was available to estimate order cost.",
            }
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
            execution = {
                **candidate,
                "order_qty": calculated_order_qty,
                "submitted": False,
                "order": None,
                "estimated_order_cost": estimated_order_cost,
                "available_buying_power": session.available_buying_power,
                "max_deployable_buying_power": session.max_deployable_buying_power,
                "remaining_deployable_buying_power": session.remaining_deployable_buying_power,
                "error": (
                    f"Estimated order cost exceeded {self._settings.max_deployable_buying_power_pct:.2f}% "
                    "buying power allowance."
                ),
            }
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
            execution = {
                **candidate,
                "order_qty": calculated_order_qty,
                "submitted": True,
                "order": order_summary,
                "estimated_order_cost": estimated_order_cost,
                "available_buying_power": session.available_buying_power,
                "max_deployable_buying_power": session.max_deployable_buying_power,
                "remaining_deployable_buying_power": session.remaining_deployable_buying_power,
                "error": "",
            }
        except Exception as exc:
            self._logger.exception("Failed to submit option order for %s: %s", option_symbol, exc)
            execution = {
                **candidate,
                "order_qty": calculated_order_qty,
                "submitted": False,
                "order": None,
                "estimated_order_cost": estimated_order_cost,
                "available_buying_power": session.available_buying_power,
                "max_deployable_buying_power": session.max_deployable_buying_power,
                "remaining_deployable_buying_power": session.remaining_deployable_buying_power,
                "error": str(exc),
            }

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


class FrontMainApplication:
    """Coordinate agent execution, trading, persistence, and loop scheduling."""

    def __init__(
        self,
        *,
        paths: FrontMainPaths,
        settings: FrontMainSettings,
        status_reporter: StatusReporter,
        cold_start_checker: ColdStartSanityChecker,
        trading_gateway: AlpacaTradingGateway,
        order_candidate_builder: OrderCandidateBuilder,
        trade_executor: OptionTradeExecutor,
        position_manager: OptionPositionManagerService,
        logger: logging.Logger,
    ) -> None:
        self._paths = paths
        self._settings = settings
        self._status_reporter = status_reporter
        self._cold_start_checker = cold_start_checker
        self._trading_gateway = trading_gateway
        self._order_candidate_builder = order_candidate_builder
        self._trade_executor = trade_executor
        self._position_manager = position_manager
        self._logger = logger

    def run_trading_cycle(self, trading_client: TradingClient | None = None) -> dict[str, Any]:
        """Run one end-to-end agent + trading cycle."""
        self._logger.info("Starting full agent stack run")
        agent_result = run_full_agent_stack()
        self._logger.info("Finished full agent stack run")

        order_candidates = self._order_candidate_builder.build(agent_result)
        self._logger.info("Prepared %s selected option candidates for trading", len(order_candidates))

        trading_client = trading_client or self._trading_gateway.create_client()
        trade_result = self._trade_executor.execute(
            trading_client=trading_client,
            order_candidates=order_candidates,
        )

        combined_result = {
            "ran_at": datetime.now().isoformat(),
            "agent_result": agent_result,
            "trade_result": trade_result,
        }

        JsonFileWriter.write(self._paths.agent_output_path, agent_result)
        JsonFileWriter.write(self._paths.selected_options_output_path, agent_result.get("selected_options", {}))
        JsonFileWriter.write(self._paths.trade_output_path, trade_result)
        JsonFileWriter.write(self._paths.combined_output_path, combined_result)

        self._logger.info("Saved agent output to %s", self._paths.agent_output_path)
        self._logger.info("Saved selected options output to %s", self._paths.selected_options_output_path)
        self._logger.info("Saved trade execution output to %s", self._paths.trade_output_path)
        self._logger.info("Saved combined front-main output to %s", self._paths.combined_output_path)

        return combined_result

    def run_streaming_trading_cycle(self, trading_client: TradingClient | None = None) -> dict[str, Any]:
        """Run the agent stack and execute qualifying options immediately per manager result."""
        trading_client = trading_client or self._trading_gateway.create_client()
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
        agent_result = run_full_agent_stack(on_manager_result=handle_manager_result)
        self._logger.info("Finished full agent stack run with immediate option execution")

        trade_result = self._trade_executor.finalize_session(trade_session)
        combined_result = {
            "ran_at": datetime.now().isoformat(),
            "agent_result": agent_result,
            "trade_result": trade_result,
        }

        JsonFileWriter.write(self._paths.agent_output_path, agent_result)
        JsonFileWriter.write(self._paths.selected_options_output_path, agent_result.get("selected_options", {}))
        JsonFileWriter.write(self._paths.trade_output_path, trade_result)
        JsonFileWriter.write(self._paths.combined_output_path, combined_result)

        self._logger.info("Saved agent output to %s", self._paths.agent_output_path)
        self._logger.info("Saved selected options output to %s", self._paths.selected_options_output_path)
        self._logger.info("Saved trade execution output to %s", self._paths.trade_output_path)
        self._logger.info("Saved combined front-main output to %s", self._paths.combined_output_path)

        return combined_result

    def run_option_position_management_cycle(self, *, trading_client: TradingClient) -> dict[str, Any]:
        """Run one option-position management cycle."""
        return self._position_manager.run_cycle(trading_client=trading_client)

    def run_main_loop(self) -> None:
        """Run the full scheduled loop that alternates trading and position management."""
        trading_client = self._trading_gateway.create_client()
        next_trading_cycle_at = datetime.now()
        next_option_management_at = datetime.now()

        self._logger.info(
            "Starting front-facing main loop with interval=%s seconds, option management interval=%s seconds, and market recheck=%s seconds",
            self._settings.run_interval_seconds,
            self._settings.option_position_management_interval_seconds,
            self._settings.market_recheck_seconds,
        )
        self._status_reporter.write(
            "starting",
            "Front-facing main loop started",
            run_interval_seconds=self._settings.run_interval_seconds,
            option_position_management_interval_seconds=self._settings.option_position_management_interval_seconds,
            market_recheck_seconds=self._settings.market_recheck_seconds,
        )

        if self._settings.cold_start_sanity_check_enabled:
            self._run_cold_start_sanity_check()

        while True:
            loop_started_at = datetime.now()

            if not self._trading_gateway.market_is_open(trading_client):
                self._logger.info(
                    "Market is closed. Sleeping %s seconds before checking again.",
                    self._settings.market_recheck_seconds,
                )
                self._status_reporter.write(
                    "paused",
                    "Market is closed",
                    sleep_seconds=self._settings.market_recheck_seconds,
                    next_check_at=datetime.now().isoformat(),
                )
                time.sleep(self._settings.market_recheck_seconds)
                continue

            current_time = datetime.now()
            if self._settings.auto_manage_option_positions and current_time >= next_option_management_at:
                try:
                    self._status_reporter.write("running", "Managing current option positions")
                    option_management_result = self.run_option_position_management_cycle(
                        trading_client=trading_client
                    )
                    self._logger.info(
                        "Option management cycle finished with %s tracked positions.",
                        option_management_result.get("position_count"),
                    )
                except Exception as exc:
                    self._status_reporter.write("error", f"Option position management failed: {exc}")
                    self._logger.exception("Option position management failed: %s", exc)
                finally:
                    next_option_management_at = datetime.now() + timedelta(
                        seconds=self._settings.option_position_management_interval_seconds
                    )

            current_time = datetime.now()
            if current_time >= next_trading_cycle_at:
                try:
                    self._status_reporter.write("running", "Executing trading cycle")
                    result = self.run_trading_cycle(trading_client=trading_client)
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
                self._logger.info(
                    "Market is closed for dedicated option manager. Sleeping %s seconds before checking again.",
                    self._settings.market_recheck_seconds,
                )
                self._status_reporter.write(
                    "paused",
                    "Market is closed",
                    sleep_seconds=self._settings.market_recheck_seconds,
                    last_loop_started_at=loop_started_at.isoformat(),
                )
                time.sleep(self._settings.market_recheck_seconds)
                continue

            current_time = datetime.now()
            if self._settings.auto_manage_option_positions and current_time >= next_option_management_at:
                try:
                    self._status_reporter.write("running", "Managing current option positions")
                    option_management_result = self.run_option_position_management_cycle(
                        trading_client=trading_client
                    )
                    self._logger.info(
                        "Dedicated option manager finished with %s tracked positions.",
                        option_management_result.get("position_count"),
                    )
                except Exception as exc:
                    self._status_reporter.write("error", f"Option position management failed: {exc}")
                    self._logger.exception("Dedicated option manager failed: %s", exc)
                finally:
                    next_option_management_at = datetime.now() + timedelta(
                        seconds=self._settings.option_position_management_interval_seconds
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


def _build_log_path(prefix: str = "front_main") -> Path:
    """Build a timestamped log file path."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return LOGS_DIR / f"{prefix}_{timestamp}.log"


def _configure_logging(log_path: Path) -> None:
    """Configure the console and file loggers for the front entrypoint."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


APP_PATHS = FrontMainPaths.from_env()
APP_SETTINGS = FrontMainSettings.from_env()
STATUS_REPORTER = StatusReporter(APP_PATHS.status_path)
TRADING_GATEWAY = AlpacaTradingGateway(paper=APP_SETTINGS.alpaca_paper, logger=LOGGER)
ORDER_CANDIDATE_BUILDER = OrderCandidateBuilder()
TRADE_EXECUTOR = OptionTradeExecutor(
    settings=APP_SETTINGS,
    trading_gateway=TRADING_GATEWAY,
    logger=LOGGER,
)
POSITION_MANAGER = OptionPositionManagerService(
    settings=APP_SETTINGS,
    paths=APP_PATHS,
    logger=LOGGER,
)
FRONT_MAIN_APP = FrontMainApplication(
    paths=APP_PATHS,
    settings=APP_SETTINGS,
    status_reporter=STATUS_REPORTER,
    cold_start_checker=ColdStartSanityChecker(LOGGER),
    trading_gateway=TRADING_GATEWAY,
    order_candidate_builder=ORDER_CANDIDATE_BUILDER,
    trade_executor=TRADE_EXECUTOR,
    position_manager=POSITION_MANAGER,
    logger=LOGGER,
)


def write_status(state: str, message: str, **extra: Any) -> None:
    """Compatibility wrapper for external callers that still use ``write_status``."""
    STATUS_REPORTER.write(state, message, **extra)


def _get_trading_client() -> TradingClient:
    """Compatibility wrapper around the Alpaca gateway."""
    return TRADING_GATEWAY.create_client()


def market_is_open(trading_client: TradingClient) -> bool:
    """Compatibility wrapper around the Alpaca market-clock check."""
    return TRADING_GATEWAY.market_is_open(trading_client)


def execute_selected_option_trades(
    *,
    trading_client: TradingClient,
    order_candidates: list[dict[str, Any]],
    order_qty: int = APP_SETTINGS.default_option_order_qty,
) -> dict[str, Any]:
    """Compatibility wrapper for the option trade executor."""
    return TRADE_EXECUTOR.execute(
        trading_client=trading_client,
        order_candidates=order_candidates,
        order_qty=order_qty,
    )


def run_option_position_management_cycle(
    *,
    trading_client: TradingClient,
) -> dict[str, Any]:
    """Compatibility wrapper for the option position manager."""
    return FRONT_MAIN_APP.run_option_position_management_cycle(trading_client=trading_client)


def main(trading_client: TradingClient | None = None) -> dict[str, Any]:
    """Run one full agent + trading cycle."""
    return FRONT_MAIN_APP.run_trading_cycle(trading_client=trading_client)


def main_loop() -> None:
    """Run the continuously scheduled front-facing main loop."""
    FRONT_MAIN_APP.run_main_loop()


def option_manager_loop() -> None:
    """Run the continuously scheduled option-management-only loop."""
    FRONT_MAIN_APP.run_option_manager_loop()


if __name__ == "__main__":
    log_path = _build_log_path()
    _configure_logging(log_path)

    LOGGER.info("Front-facing main log started at %s", log_path)
    main_loop()
    print(f"Front-facing main log written to {log_path}")
