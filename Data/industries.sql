PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS industries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    industry_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    ticker TEXT,
    headquarters TEXT,
    market_cap INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (industry_id) REFERENCES industries(id) ON DELETE CASCADE,
    UNIQUE (industry_id, name),
    UNIQUE (ticker)
);

CREATE INDEX IF NOT EXISTS idx_companies_industry_id
    ON companies (industry_id);

CREATE INDEX IF NOT EXISTS idx_companies_ticker
    ON companies (ticker);
