from __future__ import annotations

from db_common import DB_PATH, DATA_DIR
from market_db_modules.bootstrap import initialize_database
from market_db_modules.history_links import (
    get_latest_linked_manager_decision_for_option_symbol,
    get_latest_open_manager_decision_for_trade_link,
    link_manager_decision_to_trade_execution,
)
from market_db_modules.history_records import (
    expire_manager_decision_pnl,
    record_manager_decision_history,
    update_manager_decision_latest_pnl,
)
from market_db_modules.autopsy import backfill_decision_autopsy_ledger, list_decision_autopsy_ledger
from market_db_modules.manager_replay import (
    complete_manager_replay_evaluation,
    create_manager_replay_evaluation,
    list_replayable_manager_decisions,
    record_manager_replay_result,
)
from market_db_modules.entities import (
    add_company,
    add_industry,
    add_sector,
    replace_industry_company_rankings,
)
from market_db_modules.loaders import (
    load_sector_definitions,
    load_sector_definitions_from_json,
    load_sector_tree,
    load_sector_tree_from_json,
)
from market_db_modules.queries import (
    list_companies_by_industry,
    list_company_price_snapshots,
    list_industry_company_rankings,
    list_option_trade_executions,
    list_recent_manager_decision_history,
)
from market_db_modules.refresh_state import (
    get_market_data_refresh_state,
    record_market_data_refresh_state,
)
from market_db_modules.shared import SCHEMA_PATH, SECTOR_DEFINITIONS_PATH
from market_db_modules.snapshots import add_company_price_snapshot
from market_db_modules.telemetry import get_telemetry_summary, record_telemetry_installation
from market_db_modules.trade_executions import record_option_trade_execution

__all__ = [
    "DATA_DIR",
    "DB_PATH",
    "SCHEMA_PATH",
    "SECTOR_DEFINITIONS_PATH",
    "add_company",
    "add_company_price_snapshot",
    "add_industry",
    "add_sector",
    "backfill_decision_autopsy_ledger",
    "complete_manager_replay_evaluation",
    "create_manager_replay_evaluation",
    "expire_manager_decision_pnl",
    "get_latest_linked_manager_decision_for_option_symbol",
    "get_latest_open_manager_decision_for_trade_link",
    "get_market_data_refresh_state",
    "get_telemetry_summary",
    "initialize_database",
    "link_manager_decision_to_trade_execution",
    "list_decision_autopsy_ledger",
    "list_replayable_manager_decisions",
    "list_companies_by_industry",
    "list_company_price_snapshots",
    "list_industry_company_rankings",
    "list_option_trade_executions",
    "list_recent_manager_decision_history",
    "load_sector_definitions",
    "load_sector_definitions_from_json",
    "load_sector_tree",
    "load_sector_tree_from_json",
    "record_manager_decision_history",
    "record_manager_replay_result",
    "record_market_data_refresh_state",
    "record_option_trade_execution",
    "record_telemetry_installation",
    "replace_industry_company_rankings",
    "update_manager_decision_latest_pnl",
]


if __name__ == "__main__":
    initialize_database()
    json_path = DATA_DIR / "sectors_companies.json"
    if json_path.exists() and json_path.read_text(encoding="utf-8").strip():
        load_sector_tree_from_json(json_path)
        print(f"Loaded sector tree from {json_path} into {DB_PATH}")
    else:
        print(f"Initialized market database at {DB_PATH}")
