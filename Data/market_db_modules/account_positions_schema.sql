-- Positions per trading account. Followers mirror an entry of the source (primary) account.
CREATE TABLE IF NOT EXISTS account_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL, role TEXT NOT NULL, strategy TEXT NOT NULL, exit_policy TEXT NOT NULL,
    source_account TEXT NOT NULL, source_order_id TEXT NOT NULL, source_option_symbol TEXT,
    underlying TEXT, decision TEXT, legs_json TEXT NOT NULL DEFAULT '[]',
    qty INTEGER, multiplier INTEGER, unit_cost REAL, budget REAL,
    status TEXT NOT NULL CHECK(status IN ('skipped','pending_entry','open','closing','closed','failed')),
    skip_reason TEXT, error TEXT,
    entry_order_id TEXT, entry_fill_price REAL, entry_at TEXT,
    source_seen_at TEXT,
    exit_order_id TEXT, exit_reason TEXT, exit_fill_price REAL, exit_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account, source_order_id)
);
CREATE INDEX IF NOT EXISTS idx_account_positions_status ON account_positions(status);
