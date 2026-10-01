CREATE TABLE IF NOT EXISTS research_session_progress (
    session_id TEXT PRIMARY KEY REFERENCES research_sessions(session_id),
    record_hash TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json))
);
CREATE TABLE IF NOT EXISTS research_daily_allowances (
    day TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('planner', 'retrieval')),
    calls INTEGER NOT NULL CHECK(calls >= 0),
    reserved_cost REAL NOT NULL CHECK(reserved_cost >= 0),
    PRIMARY KEY(day, kind)
);
