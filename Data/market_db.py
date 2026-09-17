from __future__ import annotations

from db_common import DB_PATH, DATA_DIR
from market_db_modules.bootstrap import initialize_database
from market_db_modules.position_outcomes import (ensure_position_outcome, record_position_observation, list_post_exit_positions, resolve_post_exit_returns, exit_attribution_report, resolve_position_benchmarks, position_entry_context)
from market_db_modules.execution_telemetry import (store_execution_telemetry, pending_execution_telemetry, execution_telemetry_report)
from market_db_modules.candidate_lifecycle import (record_candidate, record_candidate_snapshot, record_candidate_rejection, load_candidate_lifecycle, candidate_funnel)
from market_db_modules.shadow_gates import backfill_gate_verdicts, list_shadow_gate_outcomes
from market_db_modules.history_links import (
    get_latest_linked_manager_decision_for_option_symbol,
    get_latest_open_manager_decision_for_trade_link,
    link_manager_decision_to_trade_execution,
)
from market_db_modules.history_records import (
    expire_manager_decision_pnl,
    record_manager_decision_closed_outcome,
    list_unresolved_manager_decisions,
    record_manager_decision_history,
    update_manager_decision_latest_pnl,
)
from market_db_modules.autopsy import backfill_decision_autopsy_ledger, list_decision_autopsy_ledger
from market_db_modules.backtesting import (
    get_backtest_run,
    list_backtest_runs,
    record_historical_option_contract,
    record_historical_option_quote,
    record_historical_underlying_bar,
    run_backtest,
)
from market_db_modules.backtest_imports import import_alpaca_option_quote_csv
from market_db_modules.backtest_marketdata import fetch_marketdata_eod_option_quotes
from market_db_modules.portfolio_risk import (
    count_option_entry_executions_since,
    get_company_sector_key,
    get_latest_stop_loss_exit_at,
    get_realized_option_loss_since,
)
from market_db_modules.manager_replay import (
    complete_manager_replay_evaluation,
    create_manager_replay_evaluation,
    list_replayable_manager_decisions,
    record_manager_replay_result,
)
from market_db_modules.market_regime import (get_latest_market_regime_snapshot, record_market_regime_snapshot, store_regime_bars, load_regime_bars)
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
    list_decision_expectancy_rows,
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
from market_db_modules.thesis_validator import (
    close_position_thesis,
    count_thesis_validator_exits_for_date,
    get_latest_thesis_validation_runs_by_option_symbol,
    get_open_position_thesis_for_option_symbol,
    get_thesis_validation_run_for_date,
    list_thesis_validation_runs,
    record_position_thesis,
    record_thesis_validation_run,
    summarize_thesis_validator_health,
)
from market_db_modules.trade_executions import record_option_trade_execution

__all__ = [
    "resolve_position_benchmarks", "position_entry_context",
    "ensure_position_outcome",
    "record_position_observation",
    "list_post_exit_positions",
    "resolve_post_exit_returns",
    "exit_attribution_report",

    "store_execution_telemetry",
    "pending_execution_telemetry",
    "execution_telemetry_report",

    "record_candidate",
    "record_candidate_snapshot",
    "record_candidate_rejection",
    "load_candidate_lifecycle",
    "candidate_funnel",

    "store_regime_bars", "load_regime_bars",
    "backfill_gate_verdicts", "list_shadow_gate_outcomes",
    "DATA_DIR",
    "DB_PATH",
    "SCHEMA_PATH",
    "SECTOR_DEFINITIONS_PATH",
    "add_company",
    "add_company_price_snapshot",
    "add_industry",
    "add_sector",
    "backfill_decision_autopsy_ledger",
    "close_position_thesis",
    "complete_manager_replay_evaluation",
    "count_thesis_validator_exits_for_date",
    "create_manager_replay_evaluation",
    "expire_manager_decision_pnl",
    "fetch_marketdata_eod_option_quotes",
    "count_option_entry_executions_since",
    "get_company_sector_key",
    "get_latest_stop_loss_exit_at",
    "get_realized_option_loss_since",
    "record_manager_decision_closed_outcome",
    "list_unresolved_manager_decisions",
    "get_latest_linked_manager_decision_for_option_symbol",
    "get_latest_open_manager_decision_for_trade_link",
    "get_latest_thesis_validation_runs_by_option_symbol",
    "get_open_position_thesis_for_option_symbol",
    "get_thesis_validation_run_for_date",
    "get_market_data_refresh_state",
    "get_latest_market_regime_snapshot",
    "get_backtest_run",
    "get_telemetry_summary",
    "initialize_database",
    "import_alpaca_option_quote_csv",
    "link_manager_decision_to_trade_execution",
    "list_decision_autopsy_ledger",
    "list_decision_expectancy_rows",
    "list_replayable_manager_decisions",
    "list_companies_by_industry",
    "list_backtest_runs",
    "list_company_price_snapshots",
    "list_industry_company_rankings",
    "list_option_trade_executions",
    "list_recent_manager_decision_history",
    "list_thesis_validation_runs",
    "load_sector_definitions",
    "load_sector_definitions_from_json",
    "load_sector_tree",
    "load_sector_tree_from_json",
    "record_manager_decision_history",
    "record_historical_option_contract",
    "record_historical_option_quote",
    "record_historical_underlying_bar",
    "record_manager_replay_result",
    "record_market_data_refresh_state",
    "record_market_regime_snapshot",
    "record_option_trade_execution",
    "record_position_thesis",
    "record_telemetry_installation",
    "record_thesis_validation_run",
    "replace_industry_company_rankings",
    "run_backtest",
    "summarize_thesis_validator_health",
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
