CREATE TABLE IF NOT EXISTS research_runs (
    run_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('frozen_forward','leakage_checked_backtest')),
    started_at TEXT NOT NULL, information_cutoff_time TEXT NOT NULL,
    target_candidates INTEGER NOT NULL, manifest_json TEXT NOT NULL,
    policy_json TEXT NOT NULL, config_hash TEXT NOT NULL, code_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','sealed'))
);
CREATE TABLE IF NOT EXISTS research_candidates (
    run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
    information_cutoff_time TEXT NOT NULL, input_hash TEXT NOT NULL, input_json TEXT NOT NULL,
    PRIMARY KEY(run_id, candidate_id)
);
CREATE TABLE IF NOT EXISTS research_shadow_positions (
    run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, strategy TEXT NOT NULL,
    information_cutoff_time TEXT NOT NULL, outcome_cutoff_time TEXT NOT NULL,
    status TEXT NOT NULL, result_json TEXT NOT NULL,
    PRIMARY KEY(run_id, candidate_id, strategy),
    FOREIGN KEY(run_id, candidate_id) REFERENCES research_candidates(run_id, candidate_id)
);
CREATE TABLE IF NOT EXISTS research_reports (
    report_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    information_cutoff_time TEXT NOT NULL, month TEXT NOT NULL, report_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_gate_promotions (
    stage TEXT NOT NULL, policy_hash TEXT NOT NULL,
    report_id TEXT NOT NULL REFERENCES research_reports(report_id),
    information_cutoff_time TEXT NOT NULL, sample_size INTEGER NOT NULL, gate_value REAL NOT NULL,
    PRIMARY KEY(stage, policy_hash)
);
CREATE TABLE IF NOT EXISTS research_champion (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    strategy TEXT NOT NULL, run_id TEXT REFERENCES research_runs(run_id),
    report_id TEXT REFERENCES research_reports(report_id), information_cutoff_time TEXT NOT NULL
);
