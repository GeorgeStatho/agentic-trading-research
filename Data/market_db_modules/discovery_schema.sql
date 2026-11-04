CREATE TABLE IF NOT EXISTS discovery_events (
    company_id INTEGER NOT NULL REFERENCES companies(id),
    catalyst_id TEXT NOT NULL REFERENCES catalysts(catalyst_id),
    evidence_revision TEXT NOT NULL, observed_at TEXT NOT NULL,
    change_kind TEXT NOT NULL CHECK(change_kind IN ('initial','material','correction')),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), payload_hash TEXT NOT NULL,
    PRIMARY KEY(company_id,catalyst_id,evidence_revision)
);
CREATE TABLE IF NOT EXISTS discovery_queue_details (
    queue_id TEXT PRIMARY KEY REFERENCES discovery_queue(queue_id),
    expires_at TEXT NOT NULL, base_priority REAL NOT NULL,
    priority_json TEXT NOT NULL CHECK(json_valid(priority_json)),
    lease_token TEXT, shadow INTEGER NOT NULL DEFAULT 1 CHECK(shadow=1)
);
CREATE TABLE IF NOT EXISTS discovery_queue_origins (
    queue_id TEXT NOT NULL REFERENCES discovery_queue(queue_id), origin TEXT NOT NULL,
    PRIMARY KEY(queue_id,origin)
);
CREATE TABLE IF NOT EXISTS discovery_shadow_candidates (
    queue_id TEXT PRIMARY KEY REFERENCES discovery_queue(queue_id),
    candidate_id TEXT NOT NULL UNIQUE REFERENCES candidates(candidate_id),
    observation_id TEXT NOT NULL REFERENCES research_observations(observation_id),
    admission_status TEXT NOT NULL CHECK(admission_status IN ('ready','deferred')),
    reason TEXT NOT NULL, payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_hash TEXT NOT NULL, created_at TEXT NOT NULL,
    shadow INTEGER NOT NULL DEFAULT 1 CHECK(shadow=1)
);
-- Admitted entry events sent to production under DISCOVERY_MODE=enforce; each is routed once.
CREATE TABLE IF NOT EXISTS discovery_production_routes (
    queue_id TEXT PRIMARY KEY REFERENCES discovery_queue(queue_id),
    symbol TEXT NOT NULL, candidate_id TEXT NOT NULL, routed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discovery_news_refreshes (
    company_id INTEGER PRIMARY KEY REFERENCES companies(id), refreshed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discovery_scan_state (
    lane TEXT NOT NULL, company_id INTEGER NOT NULL REFERENCES companies(id), scanned_at TEXT NOT NULL,
    PRIMARY KEY(lane,company_id)
);
CREATE INDEX IF NOT EXISTS idx_discovery_events_observed ON discovery_events(observed_at);
CREATE INDEX IF NOT EXISTS idx_discovery_details_expiry ON discovery_queue_details(expires_at);
CREATE TRIGGER IF NOT EXISTS discovery_events_no_update BEFORE UPDATE ON discovery_events
BEGIN SELECT RAISE(ABORT, 'Immutable discovery event'); END;
CREATE TRIGGER IF NOT EXISTS discovery_events_no_delete BEFORE DELETE ON discovery_events
BEGIN SELECT RAISE(ABORT, 'Immutable discovery event'); END;
CREATE TRIGGER IF NOT EXISTS discovery_shadow_no_update BEFORE UPDATE ON discovery_shadow_candidates
BEGIN SELECT RAISE(ABORT, 'Immutable discovery candidate'); END;
CREATE TRIGGER IF NOT EXISTS discovery_shadow_no_delete BEFORE DELETE ON discovery_shadow_candidates
BEGIN SELECT RAISE(ABORT, 'Immutable discovery candidate'); END;
