from __future__ import annotations

import logging
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
DATA_DIR = ROOT_DIR / "Data"
for path in (PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.append(normalized)

from db_helpers import DB_PATH, get_connection, initialize_market_database, list_option_trade_executions
from services.config import AlpacaSettings, ApiPaths, ApiSettings, OptionPositionSettings


logger = logging.getLogger(__name__)

API_PATHS = ApiPaths.from_env()
API_SETTINGS = ApiSettings.from_env()
OPTION_POSITION_SETTINGS = OptionPositionSettings.from_env()
ALPACA_SETTINGS = AlpacaSettings.from_env()

SCRIPT_STATUS_PATH = API_PATHS.script_status_path
TRADE_EXECUTION_OUTPUT_PATH = API_PATHS.trade_execution_output_path
AGENT_OUTPUT_PATH = API_PATHS.agent_output_path
SELECTED_OPTIONS_OUTPUT_PATH = API_PATHS.selected_options_output_path
OPTION_POSITION_MANAGEMENT_OUTPUT_PATH = API_PATHS.option_position_management_output_path
OPTION_MANAGER_STATUS_PATH = API_PATHS.option_manager_status_path

BOT_DOWN_THRESHOLD_SECONDS = API_SETTINGS.bot_status_down_threshold_seconds
DEFAULT_OPTION_ORDER_QTY = API_SETTINGS.default_option_order_qty
ANALYZED_COMPANY_NEWS_DEFAULT_PAGE_SIZE = API_SETTINGS.analyzed_company_news_default_page_size
ANALYZED_COMPANY_NEWS_MAX_PAGE_SIZE = API_SETTINGS.analyzed_company_news_max_page_size
MAX_DEPLOYABLE_BUYING_POWER_PCT = API_SETTINGS.max_deployable_buying_power_pct
PER_ORDER_SIZING_BUYING_POWER_PCT = API_SETTINGS.per_order_sizing_buying_power_pct
MAX_OPTION_ORDER_QTY_MULTIPLIER = API_SETTINGS.max_option_order_qty_multiplier
