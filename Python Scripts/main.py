from __future__ import annotations

"""Front-facing trading entrypoint.

This module is intentionally thin:
- environment/path bootstrap stays here
- service wiring stays here
- public compatibility entrypoints stay here

Implementation lives in ``Python Scripts/services``.
"""

import logging
import sys
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

from services.runtime_paths import AGENT_CALLERS_DIR, DATA_DIR, ENV_PATH, PYTHON_SCRIPTS_DIR, ROOT_DIR

for path in (PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.append(normalized)

if load_dotenv is not None:
    load_dotenv(ENV_PATH)

from services.cold_start import ColdStartSanityChecker
from services.config import FrontMainPaths, FrontMainSettings
from services.front_main_application import FrontMainApplication
from services.io_utils import JsonFileWriter, StatusReporter
from services.logging_utils import _build_log_path, _configure_logging
from services.order_candidates import OrderCandidateBuilder
from services.position_manager import OptionPositionManagerService
from services.trade_executor import OptionExposureSnapshot, OptionTradeExecutor, TradeExecutionSession
from services.trading_gateway import AlpacaTradingGateway
from services.trading_gateway import TradingClient


LOGGER = logging.getLogger("front_main")

APP_PATHS = FrontMainPaths.from_env()
APP_SETTINGS = FrontMainSettings.from_env()
STATUS_REPORTER = StatusReporter(APP_PATHS.status_path)
TRADING_GATEWAY = AlpacaTradingGateway(paper=APP_SETTINGS.alpaca_paper, logger=LOGGER)
ORDER_CANDIDATE_BUILDER = OrderCandidateBuilder(
    execute_medium_confidence_trades=APP_SETTINGS.execute_medium_confidence_trades
)
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
