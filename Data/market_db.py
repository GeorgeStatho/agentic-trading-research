from __future__ import annotations

from db_common import DB_PATH, DATA_DIR
from market_db_modules.research_runtime import replay_research_session, load_research_progress
from market_db_modules.evidence_thesis import record_evidence_thesis, get_evidence_thesis
from market_db_modules.evidence_thesis_report import evidence_thesis_report
from market_db_modules.thesis_event_reviews import (
    find_thesis_event_review, latest_thesis_verdict, record_thesis_event_review, thesis_event_review_summary,
    thesis_event_reviews,
)
# Forward cohorts and comparisons build on research_runs, which (like Part 6) stays out of this facade.
from market_db_modules.discovery_evaluation import discovery_route_ledger
from market_db_modules.research_outcomes import collect_research_thesis_outcomes, research_route_report
from market_db_modules.trade_economics import (
    record_candidate_economics_report, get_candidate_economics_report, trade_economics_summary,
)
from market_db_modules.discovery_queue import (
    enqueue_discovery_event, claim_discovery_batch, claim_position_reviews, preview_discovery_queue, renew_discovery_lease,
    finish_discovery_item, discovery_queue_status,
)
from market_db_modules.company_evidence import (
    record_evidence_document,
    record_company_fact,
    record_evidence_package,
    get_evidence_package,
    get_evidence_document,
    get_company_fact,
    latest_evidence_package,
    list_company_documents,
    list_company_facts,
)
from market_db_modules.research_foundation import (
    enqueue_discovery,
    get_discovery_item,
    record_research_session,
    get_research_session,
)
from market_db_modules.research_observations import (
    record_research_observation,
    get_research_observation,
)
from market_db_modules.catalyst_research import record_catalyst_gate_observation, freshness_outcome_report
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
    run_decision_backtest,
    daily_closes_after,
    simulate_backtest_metrics,
)
from market_db_modules.llm_replay_store import (
    get_cached_llm_replay,
    get_candidate_manifest,
    get_frozen_strategist_input,
    get_saved_bucket_chain,
    symbols_without_daily_bars,
    list_frozen_manager_decisions,
    store_cached_llm_replay,
)
from market_db_modules.backtest_imports import import_alpaca_option_quote_csv
from market_db_modules.backtest_marketdata import (
    fetch_marketdata_eod_option_quotes,
    fetch_marketdata_eod_quotes_for_contracts,
)
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
    list_open_position_theses_for_symbol,
    get_thesis_validation_run_for_date,
    list_thesis_validation_runs,
    record_position_thesis,
    record_thesis_validation_run,
    summarize_thesis_validator_health,
)
from market_db_modules.trade_executions import record_option_trade_execution

__all__ = [
    "replay_research_session",
    "load_research_progress",
    "record_evidence_thesis",
    "get_evidence_thesis",
    "evidence_thesis_report",
    "record_thesis_event_review",
    "find_thesis_event_review",
    "thesis_event_reviews",
    "latest_thesis_verdict",
    "thesis_event_review_summary",
    "discovery_route_ledger",
    "research_route_report",
    "collect_research_thesis_outcomes",
    "record_candidate_economics_report",
    "get_candidate_economics_report",
    "trade_economics_summary",
    "enqueue_discovery_event",
    "claim_discovery_batch",
    "claim_position_reviews",
    "preview_discovery_queue",
    "renew_discovery_lease",
    "finish_discovery_item",
    "discovery_queue_status",

    "record_evidence_document",
    "record_company_fact",
    "record_evidence_package",
    "get_evidence_package",
    "get_evidence_document",
    "get_company_fact",
    "latest_evidence_package",
    "list_company_documents",
    "list_company_facts",
    "enqueue_discovery",
    "get_discovery_item",
    "record_research_session",
    "get_research_session",
    "record_research_observation",
    "get_research_observation",

    "record_catalyst_gate_observation", "freshness_outcome_report",
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
    "fetch_marketdata_eod_quotes_for_contracts",
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
    "list_open_position_theses_for_symbol",
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
    "run_decision_backtest",
    "daily_closes_after",
    "get_cached_llm_replay",
    "get_candidate_manifest",
    "get_frozen_strategist_input",
    "get_saved_bucket_chain",
    "symbols_without_daily_bars",
    "list_frozen_manager_decisions",
    "store_cached_llm_replay",
    "simulate_backtest_metrics",
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
