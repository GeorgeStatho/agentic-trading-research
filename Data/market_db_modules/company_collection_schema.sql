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

CREATE TABLE IF NOT EXISTS company_evidence_requests (
    request_id TEXT PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    parent_session_id TEXT REFERENCES research_sessions(session_id),
    requested_at TEXT NOT NULL,
    next_attempt_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','unavailable')),
    attempts INTEGER NOT NULL DEFAULT 0,
    package_id TEXT REFERENCES evidence_packages(package_id),
    reason TEXT,
    completed_at TEXT,
    UNIQUE(company_id,source,source_key)
);
CREATE INDEX IF NOT EXISTS idx_evidence_requests_due ON company_evidence_requests(status,next_attempt_at,company_id);

CREATE TABLE IF NOT EXISTS research_evidence_continuations (
    request_id TEXT PRIMARY KEY REFERENCES company_evidence_requests(request_id),
    parent_session_id TEXT NOT NULL UNIQUE REFERENCES research_sessions(session_id),
    session_id TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS research_continuations_no_update BEFORE UPDATE ON research_evidence_continuations
BEGIN SELECT RAISE(ABORT, 'Immutable research continuation'); END;
CREATE TRIGGER IF NOT EXISTS research_continuations_no_delete BEFORE DELETE ON research_evidence_continuations
BEGIN SELECT RAISE(ABORT, 'Immutable research continuation'); END;
