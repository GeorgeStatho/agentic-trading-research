-- One review per open thesis per position-review queue item, i.e. per event revision. The
-- daily validator's one-run-per-date table cannot hold more than one review a day.
CREATE TABLE IF NOT EXISTS thesis_event_reviews (
    review_id TEXT PRIMARY KEY CHECK(review_id LIKE 'event-review:%'),
    thesis_id INTEGER NOT NULL REFERENCES position_theses(id),
    queue_id TEXT NOT NULL REFERENCES discovery_queue(queue_id),
    option_symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    review_day TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    thesis_state TEXT,
    would_be_action TEXT,
    record_hash TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK(json_valid(record_json)),
    UNIQUE(thesis_id, queue_id)
);
CREATE INDEX IF NOT EXISTS idx_thesis_event_reviews_day ON thesis_event_reviews(thesis_id, review_day);
CREATE INDEX IF NOT EXISTS idx_thesis_event_reviews_evidence ON thesis_event_reviews(thesis_id, evidence_hash);
CREATE INDEX IF NOT EXISTS idx_thesis_event_reviews_reviewed ON thesis_event_reviews(reviewed_at);
CREATE TRIGGER IF NOT EXISTS immutable_thesis_event_reviews_update BEFORE UPDATE ON thesis_event_reviews
BEGIN SELECT RAISE(ABORT, 'Immutable research record'); END;
CREATE TRIGGER IF NOT EXISTS immutable_thesis_event_reviews_delete BEFORE DELETE ON thesis_event_reviews
BEGIN SELECT RAISE(ABORT, 'Immutable research record'); END;
