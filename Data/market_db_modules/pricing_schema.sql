CREATE TABLE IF NOT EXISTS catalyst_response_events (
    catalyst_id TEXT PRIMARY KEY REFERENCES catalysts(catalyst_id),
    ticker TEXT NOT NULL,
    sector_symbol TEXT,
    catalyst_family TEXT NOT NULL,
    direction TEXT NOT NULL,
    event_at TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    state_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalyst_responses (
    catalyst_id TEXT NOT NULL REFERENCES catalyst_response_events(catalyst_id),
    horizon_days REAL NOT NULL,
    status TEXT NOT NULL,
    outcome_at TEXT,
    recorded_at TEXT NOT NULL,
    residual_return REAL,
    metrics_json TEXT NOT NULL,
    PRIMARY KEY (catalyst_id, horizon_days)
);
CREATE TABLE IF NOT EXISTS pricing_history_refreshes (
    symbol TEXT PRIMARY KEY,
    attempted_at TEXT NOT NULL,
    status TEXT NOT NULL,
    details_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate_price_observations (
    candidate_id TEXT PRIMARY KEY,
    catalyst_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    bucket TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    forward_residual_return REAL,
    forward_at TEXT,
    forward_checked_at TEXT,
    forward_raw_residual_return REAL
);
CREATE INDEX IF NOT EXISTS idx_catalyst_response_family ON catalyst_response_events(catalyst_family);
