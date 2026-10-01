CREATE TABLE IF NOT EXISTS evidence_thesis_checks (
    evaluation_id TEXT PRIMARY KEY,
    route TEXT NOT NULL CHECK(route IN ('legacy_strategist', 'research_shadow')),
    company_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    package_id TEXT REFERENCES evidence_packages(package_id),
    candidate_id TEXT,
    research_session_id TEXT REFERENCES research_sessions(session_id),
    created_at TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json))
);
CREATE INDEX IF NOT EXISTS idx_evidence_thesis_checks_company ON evidence_thesis_checks(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_evidence_thesis_checks_candidate ON evidence_thesis_checks(candidate_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_thesis_checks_session
    ON evidence_thesis_checks(research_session_id) WHERE research_session_id IS NOT NULL;
CREATE TRIGGER IF NOT EXISTS immutable_evidence_thesis_checks_update BEFORE UPDATE ON evidence_thesis_checks
BEGIN SELECT RAISE(ABORT, 'Immutable research record'); END;
CREATE TRIGGER IF NOT EXISTS immutable_evidence_thesis_checks_delete BEFORE DELETE ON evidence_thesis_checks
BEGIN SELECT RAISE(ABORT, 'Immutable research record'); END;
-- A reserved row without a check means a model call may have run: never retry it blindly.
CREATE TABLE IF NOT EXISTS research_thesis_progress (
    session_id TEXT PRIMARY KEY REFERENCES research_sessions(session_id),
    reserved_at TEXT NOT NULL,
    reservation_json TEXT NOT NULL CHECK(json_valid(reservation_json))
);
CREATE TABLE IF NOT EXISTS research_thesis_allowances (
    day TEXT PRIMARY KEY,
    calls INTEGER NOT NULL CHECK(calls >= 0),
    reserved_cost REAL NOT NULL CHECK(reserved_cost >= 0)
);
