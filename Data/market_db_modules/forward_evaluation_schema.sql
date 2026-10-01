-- Pre-registration for a forward cohort: which strategy it evaluates and the acceptance criteria
-- frozen when it opened. Runs without a row are manually started Part 6 runs (production role).
CREATE TABLE IF NOT EXISTS research_run_acceptance (
    run_id TEXT PRIMARY KEY REFERENCES research_runs(run_id),
    role TEXT NOT NULL CHECK(role IN ('production', 'incumbent', 'challenger')),
    cohort_id TEXT NOT NULL,
    base_pipeline_version TEXT NOT NULL,
    challenger_id TEXT,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    policy_json TEXT NOT NULL CHECK(json_valid(policy_json))
);
CREATE INDEX IF NOT EXISTS idx_research_run_acceptance_cohort ON research_run_acceptance(cohort_id, role);
CREATE INDEX IF NOT EXISTS idx_research_run_acceptance_lookup
    ON research_run_acceptance(role, base_pipeline_version, challenger_id, policy_hash);
CREATE TRIGGER IF NOT EXISTS immutable_research_run_acceptance_update BEFORE UPDATE ON research_run_acceptance
BEGIN SELECT RAISE(ABORT, 'Immutable research record'); END;
CREATE TRIGGER IF NOT EXISTS immutable_research_run_acceptance_delete BEFORE DELETE ON research_run_acceptance
BEGIN SELECT RAISE(ABORT, 'Immutable research record'); END;

-- The frozen inputs of a production decision, shared by its incumbent and challenger evaluations.
-- The production candidate is linked here, not bound: its own snapshots predate the observation.
CREATE TABLE IF NOT EXISTS decision_observations (
    candidate_id TEXT PRIMARY KEY REFERENCES candidates(candidate_id),
    observation_id TEXT REFERENCES research_observations(observation_id),
    cohort_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('recorded', 'unavailable')),
    reason TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decision_observations_cohort ON decision_observations(cohort_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_decision_observations_observation ON decision_observations(observation_id);

-- One evaluation per observation and role. `pending` rows carry retries; final rows never change.
CREATE TABLE IF NOT EXISTS paired_evaluations (
    observation_id TEXT NOT NULL REFERENCES research_observations(observation_id),
    role TEXT NOT NULL CHECK(role IN ('incumbent', 'challenger')),
    cohort_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    candidate_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending', 'evaluated', 'abstained', 'unavailable')),
    reason TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    model_calls INTEGER NOT NULL DEFAULT 0 CHECK(model_calls >= 0),
    latency_seconds REAL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(observation_id, role)
);
CREATE INDEX IF NOT EXISTS idx_paired_evaluations_cohort ON paired_evaluations(cohort_id, role, status);

CREATE TABLE IF NOT EXISTS challenger_call_allowances (
    day TEXT PRIMARY KEY,
    calls INTEGER NOT NULL CHECK(calls >= 0)
);

CREATE TABLE IF NOT EXISTS forward_comparison_reports (
    report_id TEXT PRIMARY KEY,
    cohort_id TEXT NOT NULL,
    challenger_run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    incumbent_run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    information_cutoff_time TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK(verdict IN ('interim', 'accepted', 'not_accepted')),
    report_json TEXT NOT NULL CHECK(json_valid(report_json))
);
CREATE INDEX IF NOT EXISTS idx_forward_comparison_reports_cohort
    ON forward_comparison_reports(cohort_id, information_cutoff_time);
CREATE TRIGGER IF NOT EXISTS immutable_forward_comparison_reports_update BEFORE UPDATE ON forward_comparison_reports
BEGIN SELECT RAISE(ABORT, 'Immutable research record'); END;
CREATE TRIGGER IF NOT EXISTS immutable_forward_comparison_reports_delete BEFORE DELETE ON forward_comparison_reports
BEGIN SELECT RAISE(ABORT, 'Immutable research record'); END;

CREATE TABLE IF NOT EXISTS challenger_approvals (
    approval_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL UNIQUE REFERENCES forward_comparison_reports(report_id),
    challenger_id TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json))
);
CREATE TRIGGER IF NOT EXISTS immutable_challenger_approvals_update BEFORE UPDATE ON challenger_approvals
BEGIN SELECT RAISE(ABORT, 'Immutable research record'); END;
CREATE TRIGGER IF NOT EXISTS immutable_challenger_approvals_delete BEFORE DELETE ON challenger_approvals
BEGIN SELECT RAISE(ABORT, 'Immutable research record'); END;

-- One closing-mark poll per New York session; a missed session stays a visible gap.
CREATE TABLE IF NOT EXISTS research_mark_sessions (
    session_date TEXT PRIMARY KEY,
    claimed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    summary_json TEXT
);

-- Stock-level outcome of a shadow research thesis, measured from when the thesis was written.
CREATE TABLE IF NOT EXISTS research_thesis_outcomes (
    evaluation_id TEXT PRIMARY KEY REFERENCES evidence_thesis_checks(evaluation_id),
    status TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json))
);
