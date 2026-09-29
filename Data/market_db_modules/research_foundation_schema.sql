CREATE TABLE IF NOT EXISTS evidence_documents (
    document_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    first_observed_at TEXT NOT NULL,
    supersedes_document_id TEXT REFERENCES evidence_documents(document_id),
    content_hash TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json))
);
CREATE INDEX IF NOT EXISTS idx_evidence_documents_observed
    ON evidence_documents(provider, first_observed_at);

CREATE TABLE IF NOT EXISTS company_facts (
    fact_id TEXT PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    document_id TEXT NOT NULL REFERENCES evidence_documents(document_id),
    metric TEXT NOT NULL,
    fiscal_period TEXT NOT NULL,
    available_at TEXT NOT NULL,
    supersedes_fact_id TEXT REFERENCES company_facts(fact_id),
    record_hash TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json))
);
CREATE INDEX IF NOT EXISTS idx_company_facts_lookup
    ON company_facts(company_id, metric, fiscal_period, available_at);

CREATE TABLE IF NOT EXISTS evidence_packages (
    package_id TEXT PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    catalyst_id TEXT NOT NULL REFERENCES catalysts(catalyst_id),
    event_revision TEXT NOT NULL,
    information_cutoff_time TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json))
);
CREATE INDEX IF NOT EXISTS idx_evidence_packages_company_cutoff
    ON evidence_packages(company_id, information_cutoff_time);
CREATE TABLE IF NOT EXISTS evidence_package_documents (
    package_id TEXT NOT NULL REFERENCES evidence_packages(package_id),
    document_id TEXT NOT NULL REFERENCES evidence_documents(document_id),
    PRIMARY KEY(package_id, document_id)
);
CREATE TABLE IF NOT EXISTS evidence_package_facts (
    package_id TEXT NOT NULL REFERENCES evidence_packages(package_id),
    fact_id TEXT NOT NULL REFERENCES company_facts(fact_id),
    PRIMARY KEY(package_id, fact_id)
);

CREATE TABLE IF NOT EXISTS discovery_queue (
    queue_id TEXT PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    catalyst_id TEXT NOT NULL REFERENCES catalysts(catalyst_id),
    evidence_revision TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK(purpose IN ('entry','watchlist','position_review')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','running','completed','deferred','expired','failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    next_attempt_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    completion_reason TEXT,
    record_hash TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json)),
    UNIQUE(company_id, catalyst_id, evidence_revision, purpose)
);
CREATE INDEX IF NOT EXISTS idx_discovery_queue_due
    ON discovery_queue(status, next_attempt_at, lease_expires_at);

CREATE TABLE IF NOT EXISTS research_sessions (
    session_id TEXT PRIMARY KEY,
    queue_id TEXT NOT NULL REFERENCES discovery_queue(queue_id),
    initial_package_id TEXT NOT NULL REFERENCES evidence_packages(package_id),
    final_package_id TEXT REFERENCES evidence_packages(package_id),
    status TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json))
);
CREATE INDEX IF NOT EXISTS idx_research_sessions_queue ON research_sessions(queue_id, status);
CREATE TABLE IF NOT EXISTS research_steps (
    session_id TEXT NOT NULL REFERENCES research_sessions(session_id),
    step_number INTEGER NOT NULL CHECK(step_number >= 0),
    record_hash TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json)),
    PRIMARY KEY(session_id, step_number)
);

CREATE TABLE IF NOT EXISTS research_observations (
    observation_id TEXT PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    symbol TEXT NOT NULL,
    catalyst_id TEXT NOT NULL REFERENCES catalysts(catalyst_id),
    event_revision TEXT NOT NULL,
    information_cutoff_time TEXT NOT NULL,
    protocol_version TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json))
);
CREATE INDEX IF NOT EXISTS idx_research_observations_event
    ON research_observations(company_id, catalyst_id, event_revision, information_cutoff_time);
CREATE TABLE IF NOT EXISTS candidate_observations (
    candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id),
    observation_id TEXT NOT NULL REFERENCES research_observations(observation_id)
);
CREATE INDEX IF NOT EXISTS idx_candidate_observations_observation
    ON candidate_observations(observation_id);

-- Separate tables preserve legacy run hashes and positional INSERT contracts.
CREATE TABLE IF NOT EXISTS research_run_versions (
    run_id TEXT PRIMARY KEY REFERENCES research_runs(run_id),
    version TEXT NOT NULL,
    evaluator_hash TEXT NOT NULL,
    protocol_version TEXT NOT NULL,
    version_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    observation_id TEXT NOT NULL REFERENCES research_observations(observation_id),
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
    strategy TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('evaluated','abstained','unavailable')),
    UNIQUE(run_id, observation_id),
    UNIQUE(run_id, candidate_id),
    FOREIGN KEY(run_id, candidate_id) REFERENCES research_candidates(run_id, candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_observation
    ON strategy_evaluations(observation_id, strategy);
CREATE TABLE IF NOT EXISTS trade_economics_reports (
    report_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
    package_id TEXT NOT NULL REFERENCES evidence_packages(package_id),
    evaluation_id TEXT REFERENCES strategy_evaluations(evaluation_id),
    created_at TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json))
);
CREATE INDEX IF NOT EXISTS idx_trade_economics_candidate
    ON trade_economics_reports(candidate_id, created_at);
