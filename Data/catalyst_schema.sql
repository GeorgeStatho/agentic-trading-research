CREATE TABLE IF NOT EXISTS catalysts (
    catalyst_id TEXT PRIMARY KEY CHECK(catalyst_id LIKE 'catalyst:%'),
    ticker TEXT, catalyst_type TEXT NOT NULL, catalyst_subtype TEXT NOT NULL,
    catalyst_family TEXT NOT NULL, catalyst_scope TEXT NOT NULL, direction TEXT NOT NULL,
    mechanism TEXT NOT NULL, expected_horizon TEXT NOT NULL,
    first_public_time TEXT, first_seen_time TEXT, latest_evidence_time TEXT, latest_root_event_time TEXT,
    article_count INTEGER NOT NULL, source_count INTEGER NOT NULL, independent_root_count INTEGER NOT NULL,
    primary_source_present INTEGER NOT NULL, evidence_score REAL,
    expected_half_life_minutes INTEGER, classification_version TEXT NOT NULL,
    classification_status TEXT NOT NULL, state_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS root_events (
    root_event_id TEXT PRIMARY KEY CHECK(root_event_id LIKE 'root:%'),
    catalyst_id TEXT NOT NULL REFERENCES catalysts(catalyst_id), root_source_id TEXT NOT NULL,
    first_public_time TEXT, first_seen_time TEXT, verified INTEGER NOT NULL, primary_source_present INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate_catalysts (
    candidate_id TEXT NOT NULL, catalyst_id TEXT NOT NULL REFERENCES catalysts(catalyst_id),
    catalyst_family TEXT NOT NULL, is_primary INTEGER NOT NULL, state_json TEXT NOT NULL,
    PRIMARY KEY(candidate_id, catalyst_id)
);
CREATE TABLE IF NOT EXISTS catalyst_gate_observations (
    candidate_id TEXT NOT NULL, catalyst_id TEXT NOT NULL REFERENCES catalysts(catalyst_id),
    stage TEXT NOT NULL, observed_at TEXT NOT NULL, age_minutes REAL, remaining_value REAL,
    would_allow INTEGER NOT NULL, allowed INTEGER NOT NULL, shadow INTEGER NOT NULL,
    policy_version TEXT NOT NULL, metrics_json TEXT NOT NULL, thresholds_json TEXT NOT NULL,
    PRIMARY KEY(candidate_id, stage)
);
CREATE INDEX IF NOT EXISTS idx_catalysts_family_time ON catalysts(catalyst_family, first_public_time);
CREATE INDEX IF NOT EXISTS idx_roots_catalyst ON root_events(catalyst_id);
CREATE INDEX IF NOT EXISTS idx_candidate_catalysts_catalyst ON candidate_catalysts(catalyst_id);
CREATE TABLE IF NOT EXISTS catalyst_model_labels (
    catalyst_id TEXT NOT NULL, label_version TEXT NOT NULL,
    catalyst_type TEXT NOT NULL, direction TEXT NOT NULL, confidence TEXT NOT NULL,
    anchor_article_id INTEGER, model TEXT NOT NULL, answered_by_json TEXT NOT NULL,
    labeled_at TEXT NOT NULL, PRIMARY KEY (catalyst_id, label_version, labeled_at)
);
CREATE INDEX IF NOT EXISTS idx_catalyst_model_labels_time ON catalyst_model_labels(catalyst_id, labeled_at);
CREATE TRIGGER IF NOT EXISTS catalyst_model_labels_no_update BEFORE UPDATE ON catalyst_model_labels
BEGIN SELECT RAISE(ABORT, 'Immutable catalyst label'); END;
CREATE TRIGGER IF NOT EXISTS catalyst_model_labels_no_delete BEFORE DELETE ON catalyst_model_labels
BEGIN SELECT RAISE(ABORT, 'Immutable catalyst label'); END;
