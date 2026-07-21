PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sectors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sector_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS industries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sector_id INTEGER NOT NULL,
    industry_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    symbol TEXT,
    market_weight REAL,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    industry_id INTEGER NOT NULL,
    symbol TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    rating TEXT,
    market_weight REAL,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (industry_id) REFERENCES industries(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS industry_company_rankings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    industry_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    ranking_type TEXT NOT NULL,
    rank INTEGER NOT NULL,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (industry_id) REFERENCES industries(id) ON DELETE CASCADE,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    UNIQUE (industry_id, company_id, ranking_type)
);

CREATE TABLE IF NOT EXISTS market_data_refresh_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_type TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    last_hydrated_at TEXT NOT NULL,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (resource_type, resource_key)
);

CREATE TABLE IF NOT EXISTS company_price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    price REAL,
    previous_price REAL,
    price_change REAL,
    price_change_pct REAL,
    volume REAL,
    source TEXT,
    raw_json TEXT,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS option_trade_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER,
    order_id TEXT NOT NULL UNIQUE,
    underlying_symbol TEXT,
    company_name TEXT,
    option_symbol TEXT NOT NULL,
    decision TEXT,
    confidence TEXT,
    selected_option_id TEXT,
    selected_option_source TEXT,
    expiration_date TEXT,
    strike_price REAL,
    order_qty INTEGER,
    estimated_order_cost REAL,
    available_buying_power REAL,
    max_deployable_buying_power REAL,
    remaining_deployable_buying_power REAL,
    paper INTEGER NOT NULL DEFAULT 1,
    order_status TEXT,
    order_side TEXT,
    order_type TEXT,
    time_in_force TEXT,
    submitted_at TEXT,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    raw_json TEXT,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS manager_decision_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER,
    symbol TEXT,
    company_name TEXT,
    decision_run_at TEXT NOT NULL,
    manager_stage_version TEXT,
    strategist_decision TEXT,
    manager_decision TEXT,
    manager_confidence TEXT,
    manager_reason TEXT,
    target_dte_bucket TEXT,
    selected_option_id TEXT,
    selected_option_symbol TEXT,
    selected_expiration_date TEXT,
    selected_strike_price REAL,
    selected_option_source TEXT,
    manager_input_json TEXT,
    manager_output_json TEXT,
    trade_executed INTEGER NOT NULL DEFAULT 0,
    trade_execution_order_id TEXT,
    trade_execution_record_id INTEGER,
    latest_trade_pnl_pct REAL,
    latest_trade_pnl_updated_at TEXT,
    pnl_expires_at TEXT,
    resolved_outcome_label TEXT,
    outcome_status TEXT,
    realized_pnl_pct REAL,
    closed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS decision_autopsy_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manager_decision_history_id INTEGER NOT NULL UNIQUE,
    symbol TEXT,
    decision_run_at TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (manager_decision_history_id) REFERENCES manager_decision_history(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS manager_replay_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    autopsy_limit INTEGER NOT NULL,
    autopsy_max_age_days INTEGER NOT NULL,
    source_decision_count INTEGER NOT NULL DEFAULT 0,
    replayed_decision_count INTEGER NOT NULL DEFAULT 0,
    skipped_decision_count INTEGER NOT NULL DEFAULT 0,
    metrics_json TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS manager_replay_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id INTEGER NOT NULL,
    manager_decision_history_id INTEGER NOT NULL,
    replay_mode TEXT NOT NULL,
    decision_run_at TEXT NOT NULL,
    autopsy_ids_json TEXT NOT NULL DEFAULT '[]',
    recommendation_json TEXT,
    error_text TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (evaluation_id) REFERENCES manager_replay_evaluations(id) ON DELETE CASCADE,
    FOREIGN KEY (manager_decision_history_id) REFERENCES manager_decision_history(id) ON DELETE CASCADE,
    UNIQUE (evaluation_id, manager_decision_history_id, replay_mode)
);

CREATE TABLE IF NOT EXISTS decision_evidence_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manager_decision_history_id INTEGER NOT NULL UNIQUE,
    as_of TEXT NOT NULL,
    snapshot_version TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    article_ids_json TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (manager_decision_history_id) REFERENCES manager_decision_history(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS historical_underlying_bars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    bar_at TEXT NOT NULL,
    timeframe TEXT NOT NULL DEFAULT '1d',
    open_price REAL,
    high_price REAL,
    low_price REAL,
    close_price REAL,
    volume REAL,
    provider TEXT NOT NULL,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (symbol, bar_at, timeframe, provider)
);

CREATE TABLE IF NOT EXISTS historical_option_contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    option_symbol TEXT NOT NULL,
    underlying_symbol TEXT NOT NULL,
    contract_type TEXT,
    expiration_date TEXT,
    strike_price REAL,
    provider TEXT NOT NULL,
    metadata_json TEXT,
    captured_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (option_symbol, provider, captured_at)
);

CREATE TABLE IF NOT EXISTS historical_option_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    option_symbol TEXT NOT NULL,
    quote_at TEXT NOT NULL,
    bid_price REAL,
    ask_price REAL,
    last_price REAL,
    volume REAL,
    open_interest REAL,
    implied_volatility REAL,
    delta REAL,
    gamma REAL,
    theta REAL,
    vega REAL,
    provider TEXT NOT NULL,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (option_symbol, quote_at, provider)
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_version TEXT NOT NULL,
    model TEXT,
    prompt_version TEXT,
    config_json TEXT NOT NULL,
    evidence_snapshot_version TEXT NOT NULL,
    data_source_versions_json TEXT NOT NULL,
    code_revision TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    metrics_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backtest_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    as_of TEXT NOT NULL,
    manager_decision_history_id INTEGER,
    evidence_snapshot_id INTEGER,
    symbol TEXT,
    decision TEXT,
    option_symbol TEXT,
    status TEXT NOT NULL,
    rationale TEXT,
    entry_at TEXT,
    exit_at TEXT,
    entry_price REAL,
    exit_price REAL,
    quantity INTEGER,
    realized_pnl_pct REAL,
    realized_pnl_dollars REAL,
    commission_dollars REAL,
    slippage_dollars REAL,
    warnings_json TEXT,
    event_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES backtest_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (manager_decision_history_id) REFERENCES manager_decision_history(id) ON DELETE SET NULL,
    FOREIGN KEY (evidence_snapshot_id) REFERENCES decision_evidence_snapshots(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS telemetry_installations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    install_id TEXT NOT NULL UNIQUE,
    project TEXT,
    telemetry_version INTEGER,
    install_created_at TEXT,
    first_runtime TEXT,
    last_runtime TEXT,
    first_release TEXT,
    last_release TEXT,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_started_at TEXT,
    startup_ping_count INTEGER NOT NULL DEFAULT 1,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_sectors_sector_key
    ON sectors (sector_key);

CREATE INDEX IF NOT EXISTS idx_industries_sector_id
    ON industries (sector_id);

CREATE INDEX IF NOT EXISTS idx_industries_industry_key
    ON industries (industry_key);

CREATE INDEX IF NOT EXISTS idx_companies_industry_id
    ON companies (industry_id);

CREATE INDEX IF NOT EXISTS idx_companies_symbol
    ON companies (symbol);

CREATE INDEX IF NOT EXISTS idx_industry_company_rankings_industry_id
    ON industry_company_rankings (industry_id);

CREATE INDEX IF NOT EXISTS idx_industry_company_rankings_company_id
    ON industry_company_rankings (company_id);

CREATE INDEX IF NOT EXISTS idx_industry_company_rankings_type
    ON industry_company_rankings (ranking_type);

CREATE INDEX IF NOT EXISTS idx_market_data_refresh_state_resource
    ON market_data_refresh_state (resource_type, resource_key);

CREATE INDEX IF NOT EXISTS idx_company_price_snapshots_company_id
    ON company_price_snapshots (company_id);

CREATE INDEX IF NOT EXISTS idx_company_price_snapshots_symbol
    ON company_price_snapshots (symbol);

CREATE INDEX IF NOT EXISTS idx_company_price_snapshots_captured_at
    ON company_price_snapshots (captured_at);

CREATE INDEX IF NOT EXISTS idx_option_trade_executions_company_id
    ON option_trade_executions (company_id);

CREATE INDEX IF NOT EXISTS idx_option_trade_executions_underlying_symbol
    ON option_trade_executions (underlying_symbol);

CREATE INDEX IF NOT EXISTS idx_option_trade_executions_option_symbol
    ON option_trade_executions (option_symbol);

CREATE INDEX IF NOT EXISTS idx_option_trade_executions_submitted_at
    ON option_trade_executions (submitted_at);

CREATE INDEX IF NOT EXISTS idx_manager_decision_history_company_id
    ON manager_decision_history (company_id);

CREATE INDEX IF NOT EXISTS idx_manager_decision_history_symbol
    ON manager_decision_history (symbol);

CREATE INDEX IF NOT EXISTS idx_manager_decision_history_decision_run_at
    ON manager_decision_history (decision_run_at);

CREATE INDEX IF NOT EXISTS idx_manager_decision_history_trade_execution_order_id
    ON manager_decision_history (trade_execution_order_id);

CREATE INDEX IF NOT EXISTS idx_manager_decision_history_trade_execution_record_id
    ON manager_decision_history (trade_execution_record_id);

CREATE INDEX IF NOT EXISTS idx_manager_decision_history_selected_option_symbol
    ON manager_decision_history (selected_option_symbol);

CREATE INDEX IF NOT EXISTS idx_decision_autopsy_ledger_run_at
    ON decision_autopsy_ledger (decision_run_at DESC);

CREATE INDEX IF NOT EXISTS idx_manager_replay_results_evaluation
    ON manager_replay_results (evaluation_id, replay_mode);

CREATE INDEX IF NOT EXISTS idx_manager_replay_results_history
    ON manager_replay_results (manager_decision_history_id);

CREATE INDEX IF NOT EXISTS idx_decision_evidence_snapshots_history
    ON decision_evidence_snapshots (manager_decision_history_id);

CREATE INDEX IF NOT EXISTS idx_historical_underlying_bars_symbol_at
    ON historical_underlying_bars (symbol, bar_at);

CREATE INDEX IF NOT EXISTS idx_historical_option_quotes_symbol_at
    ON historical_option_quotes (option_symbol, quote_at);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_created_at
    ON backtest_runs (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_backtest_events_run_as_of
    ON backtest_events (run_id, as_of);

CREATE INDEX IF NOT EXISTS idx_telemetry_installations_project
    ON telemetry_installations (project);

CREATE INDEX IF NOT EXISTS idx_telemetry_installations_last_seen_at
    ON telemetry_installations (last_seen_at);
