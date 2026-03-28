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
