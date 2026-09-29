CREATE TABLE IF NOT EXISTS company_provider_responses (
    response_id TEXT PRIMARY KEY, provider TEXT NOT NULL, url TEXT NOT NULL,
    observed_at TEXT NOT NULL, body TEXT NOT NULL, content_hash TEXT NOT NULL,
    UNIQUE(provider,url,content_hash)
);
CREATE INDEX IF NOT EXISTS idx_company_responses_url ON company_provider_responses(provider,url,observed_at);
CREATE TABLE IF NOT EXISTS company_collection_state (
    provider TEXT NOT NULL, scope TEXT NOT NULL, value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL, PRIMARY KEY(provider,scope)
);
CREATE TABLE IF NOT EXISTS company_provider_usage (
    provider TEXT NOT NULL, day TEXT NOT NULL, requests INTEGER NOT NULL,
    next_request_at REAL NOT NULL, PRIMARY KEY(provider,day)
);
CREATE TABLE IF NOT EXISTS company_issuer_mappings (
    company_id INTEGER PRIMARY KEY REFERENCES companies(id), symbol TEXT NOT NULL,
    cik TEXT NOT NULL, source TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS company_responses_no_update BEFORE UPDATE ON company_provider_responses
BEGIN SELECT RAISE(ABORT, 'Immutable provider response'); END;
CREATE TRIGGER IF NOT EXISTS company_responses_no_delete BEFORE DELETE ON company_provider_responses
BEGIN SELECT RAISE(ABORT, 'Immutable provider response'); END;
