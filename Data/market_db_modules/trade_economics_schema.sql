-- Decision-time economics for any candidate; an evidence package is optional because most
-- legacy-route candidates have none.
CREATE TABLE IF NOT EXISTS candidate_economics_reports (
    report_id TEXT PRIMARY KEY CHECK(report_id LIKE 'economics:%'),
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
    package_id TEXT REFERENCES evidence_packages(package_id),
    catalyst_id TEXT,
    selected_option_symbol TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('trade', 'no_trade', 'unavailable')),
    policy_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json))
);
CREATE INDEX IF NOT EXISTS idx_candidate_economics_candidate ON candidate_economics_reports(candidate_id, created_at);
CREATE INDEX IF NOT EXISTS idx_candidate_economics_created ON candidate_economics_reports(created_at);
CREATE TRIGGER IF NOT EXISTS immutable_candidate_economics_reports_update BEFORE UPDATE ON candidate_economics_reports
BEGIN SELECT RAISE(ABORT, 'Immutable research record'); END;
CREATE TRIGGER IF NOT EXISTS immutable_candidate_economics_reports_delete BEFORE DELETE ON candidate_economics_reports
BEGIN SELECT RAISE(ABORT, 'Immutable research record'); END;
