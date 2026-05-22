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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL
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

CREATE INDEX IF NOT EXISTS idx_manager_decision_history_selected_option_symbol
    ON manager_decision_history (selected_option_symbol);
