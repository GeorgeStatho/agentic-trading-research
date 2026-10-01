CREATE TABLE IF NOT EXISTS company_interest (
    company_id INTEGER NOT NULL REFERENCES companies(id),
    source TEXT NOT NULL, source_key TEXT NOT NULL,
    selected_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    PRIMARY KEY(company_id,source,source_key)
);
CREATE INDEX IF NOT EXISTS idx_company_interest_expiry ON company_interest(expires_at,company_id);
CREATE TABLE IF NOT EXISTS discovery_check_signals (
    company_id INTEGER NOT NULL REFERENCES companies(id), source TEXT NOT NULL,
    version TEXT NOT NULL, observed_at TEXT NOT NULL,
    PRIMARY KEY(company_id,source)
);
CREATE TABLE IF NOT EXISTS discovery_checks (
    company_id INTEGER PRIMARY KEY REFERENCES companies(id),
    generation INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_at TEXT NOT NULL, next_check_at TEXT NOT NULL,
    checked_at TEXT, reason TEXT,
    dependency_request_id TEXT REFERENCES company_evidence_requests(request_id)
);
CREATE INDEX IF NOT EXISTS idx_discovery_checks_due ON discovery_checks(next_check_at,status);
CREATE TABLE IF NOT EXISTS research_work_owners (
    work_key TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    catalyst_id TEXT NOT NULL, purpose TEXT NOT NULL,
    input_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS research_work_owners_no_update BEFORE UPDATE ON research_work_owners
BEGIN SELECT RAISE(ABORT, 'Immutable research work owner'); END;
CREATE TRIGGER IF NOT EXISTS research_work_owners_no_delete BEFORE DELETE ON research_work_owners
BEGIN SELECT RAISE(ABORT, 'Immutable research work owner'); END;
