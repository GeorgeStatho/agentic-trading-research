from __future__ import annotations

"""Front-facing trading entrypoint.

This module is intentionally thin:
- environment/path bootstrap stays here
- service wiring stays here
- public compatibility entrypoints stay here

Implementation lives in ``Python Scripts/services``.
"""

import logging
from typing import Any

from services.core.settings import load_process_settings

# Entrypoint only: importers (the option-manager entrypoint, tests) must not load .env.
if __name__ == "__main__":
    load_process_settings()

from services.runtime.cold_start import ColdStartSanityChecker
from services.core.trading_config import WorkerPaths, WorkerSettings
from services.runtime.worker_application import WorkerApplication
from services.core.status_files import StatusReporter
from services.core.logging_utils import configure_logging
from services.trading.order_candidates import OrderCandidateBuilder
from services.options.position_manager import OptionPositionManagerService
from services.thesis.thesis_capture import PositionThesisJournal
from services.thesis.thesis_validator_service import ThesisValidatorService
from services.thesis.thesis_event_review import EventThesisReviewService
from services.trading.trade_journal import OptionTradeJournal
from services.trading.trade_executor import OptionTradeExecutor
from services.trading.trading_gateway import AlpacaTradingGateway
from services.trading.follower_fleet import build_follower_fleet
from services.trading.trading_accounts import load_trading_accounts
from services.trading.trading_gateway import TradingClient


LOGGER = logging.getLogger("front_main")

APP_PATHS = WorkerPaths.from_env()
APP_SETTINGS = WorkerSettings.from_env()
STATUS_REPORTER = StatusReporter(APP_PATHS.status_path)
TRADING_GATEWAY = AlpacaTradingGateway(paper=APP_SETTINGS.alpaca_paper, logger=LOGGER)
# None unless follower accounts are configured; production alone runs exactly as before.
FOLLOWER_FLEET = build_follower_fleet(load_trading_accounts(), logger=LOGGER, primary_gateway=TRADING_GATEWAY)
ORDER_CANDIDATE_BUILDER = OrderCandidateBuilder(
    execute_medium_confidence_trades=APP_SETTINGS.execute_medium_confidence_trades
)
TRADE_JOURNAL = OptionTradeJournal(
    paper=APP_SETTINGS.alpaca_paper,
    logger=LOGGER,
)
THESIS_JOURNAL = PositionThesisJournal(logger=LOGGER)
THESIS_VALIDATOR = ThesisValidatorService(logger=LOGGER)
EVENT_THESIS_REVIEWER = EventThesisReviewService(validator_settings=THESIS_VALIDATOR.settings, logger=LOGGER)
TRADE_EXECUTOR = OptionTradeExecutor(
    settings=APP_SETTINGS,
    trading_gateway=TRADING_GATEWAY,
    trade_journal=TRADE_JOURNAL,
    thesis_journal=THESIS_JOURNAL,
    follower_fleet=FOLLOWER_FLEET,
    logger=LOGGER,
)
POSITION_MANAGER = OptionPositionManagerService(
    settings=APP_SETTINGS,
    paths=APP_PATHS,
    trading_gateway=TRADING_GATEWAY,
    trade_journal=TRADE_JOURNAL,
    thesis_journal=THESIS_JOURNAL,
    thesis_validator=THESIS_VALIDATOR,
    event_thesis_reviewer=EVENT_THESIS_REVIEWER,
    logger=LOGGER,
)
FRONT_MAIN_APP = WorkerApplication(
    paths=APP_PATHS,
    settings=APP_SETTINGS,
    status_reporter=STATUS_REPORTER,
    cold_start_checker=ColdStartSanityChecker(LOGGER),
    trading_gateway=TRADING_GATEWAY,
    order_candidate_builder=ORDER_CANDIDATE_BUILDER,
    trade_executor=TRADE_EXECUTOR,
    position_manager=POSITION_MANAGER,
    logger=LOGGER,
    follower_fleet=FOLLOWER_FLEET,
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
    log_path = configure_logging("front_main")

    LOGGER.info("Front-facing main log started at %s", log_path)
    main_loop()
    print(f"Front-facing main log written to {log_path}")
