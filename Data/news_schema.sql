PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS macro_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    event_key TEXT NOT NULL UNIQUE,
    event_name TEXT NOT NULL,
    event_date TEXT,
    event_time TEXT,
    scheduled_at_utc TEXT,
    calendar_timezone TEXT,
    calendar_refreshed_at TEXT,
    country TEXT,
    currency TEXT,
    category TEXT,
    importance TEXT,
    actual TEXT,
    forecast TEXT,
    previous TEXT,
    source_url TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS news_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    article_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    normalized_title TEXT,
    source_type TEXT,
    primary_ticker TEXT,
    catalyst_id TEXT,
    root_event_id TEXT,
    root_source_id TEXT,
    syndication_parent_id INTEGER,
    primary_source_url TEXT,
    event_time TEXT,
    first_seen_time TEXT,
    content_updated_at TEXT,

    summary TEXT,
    normalized_url TEXT,
    content_hash TEXT,
    published_at TEXT,
    section TEXT,
    age_days REAL,
    recency_score REAL,
    source_reputation_score REAL,
    directness_score REAL,
    confirmation_score REAL,
    independent_source_count INTEGER,
    factuality_score REAL,
    evidence_score REAL,
    source_url TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- The article text and the scraped payload (which repeats the text) are kept apart from
-- news_articles so the many queries that filter or sort on its metadata never page
-- through them.
CREATE TABLE IF NOT EXISTS news_article_content (
    article_id INTEGER PRIMARY KEY,
    body TEXT,
    raw_json TEXT,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS industry_news_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    industry_id INTEGER NOT NULL,
    article_id INTEGER NOT NULL,
    source_page_url TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (industry_id) REFERENCES industries(id) ON DELETE CASCADE,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE,
    UNIQUE (industry_id, article_id)
);

CREATE TABLE IF NOT EXISTS company_news_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    article_id INTEGER NOT NULL,
    source_page_url TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE,
    UNIQUE (company_id, article_id)
);

CREATE TABLE IF NOT EXISTS sector_news_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sector_id INTEGER NOT NULL,
    article_id INTEGER NOT NULL,
    source_page_url TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE,
    UNIQUE (sector_id, article_id)
);

CREATE TABLE IF NOT EXISTS us_news_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL UNIQUE,
    source_page_url TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS world_news_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL UNIQUE,
    source_page_url TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS world_news_sector_impacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    sector_id INTEGER NOT NULL,
    confidence TEXT,
    reason TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE,
    FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
    UNIQUE (article_id, sector_id)
);

CREATE TABLE IF NOT EXISTS world_news_article_processing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL UNIQUE,
    model TEXT,
    raw_json TEXT,
    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS us_news_sector_impacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    sector_id INTEGER NOT NULL,
    confidence TEXT,
    reason TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE,
    FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
    UNIQUE (article_id, sector_id)
);

CREATE TABLE IF NOT EXISTS us_news_article_processing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL UNIQUE,
    model TEXT,
    raw_json TEXT,
    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS industry_opportunist_impacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    industry_id INTEGER NOT NULL,
    confidence TEXT,
    impact_direction TEXT,
    impact_magnitude TEXT,
    reason TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE,
    FOREIGN KEY (industry_id) REFERENCES industries(id) ON DELETE CASCADE,
    UNIQUE (article_id, industry_id)
);

CREATE TABLE IF NOT EXISTS industry_opportunist_article_processing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL UNIQUE,
    model TEXT,
    raw_json TEXT,
    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_opportunist_impacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    confidence TEXT,
    impact_direction TEXT,
    impact_magnitude TEXT,
    reason TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    UNIQUE (article_id, company_id)
);

CREATE TABLE IF NOT EXISTS company_opportunist_article_processing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    model TEXT,
    raw_json TEXT,
    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    UNIQUE (article_id, company_id)
);

CREATE TABLE IF NOT EXISTS sector_opportunist_impacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    sector_id INTEGER NOT NULL,
    confidence TEXT,
    impact_direction TEXT,
    impact_magnitude TEXT,
    reason TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE,
    FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE,
    UNIQUE (article_id, sector_id)
);

CREATE TABLE IF NOT EXISTS sector_opportunist_article_processing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL UNIQUE,
    model TEXT,
    raw_json TEXT,
    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS strategist_company_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL UNIQUE,
    decision TEXT,
    confidence TEXT,
    summary TEXT,
    thesis TEXT,
    risks TEXT,
    model TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS failed_urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    normalized_url TEXT NOT NULL UNIQUE,
    stage TEXT,
    last_error TEXT,
    failure_count INTEGER NOT NULL DEFAULT 1,
    is_permanent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategist_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT,
    company_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_article_ids_json TEXT NOT NULL,
    input_payload_json TEXT NOT NULL,
    output_payload_json TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS news_exposure_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_article_ids_json TEXT NOT NULL,
    article_summary TEXT,
    uncertainties_json TEXT,
    raw_response_json TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES news_articles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS news_exposure_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    from_type TEXT NOT NULL,
    from_id TEXT NOT NULL,
    to_type TEXT NOT NULL,
    to_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    magnitude TEXT NOT NULL,
    confidence TEXT NOT NULL,
    mechanism TEXT NOT NULL,
    evidence_article_ids_json TEXT NOT NULL,
    exposure_score REAL NOT NULL DEFAULT 0,
    score_inputs_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES news_exposure_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS news_exposure_position_impacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    option_symbol TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    reason TEXT NOT NULL,
    risk_score REAL NOT NULL DEFAULT 0,
    score_inputs_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES news_exposure_runs(id) ON DELETE CASCADE,
    UNIQUE (run_id, option_symbol)
);

CREATE INDEX IF NOT EXISTS idx_macro_events_event_date
    ON macro_events (event_date);

CREATE INDEX IF NOT EXISTS idx_macro_events_category
    ON macro_events (category);

CREATE INDEX IF NOT EXISTS idx_macro_events_scheduled_at_utc
    ON macro_events (scheduled_at_utc);

CREATE INDEX IF NOT EXISTS idx_news_articles_published_at
    ON news_articles (published_at);

CREATE INDEX IF NOT EXISTS idx_news_exposure_runs_article_created
    ON news_exposure_runs (article_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_news_exposure_edges_run
    ON news_exposure_edges (run_id);

CREATE INDEX IF NOT EXISTS idx_news_exposure_position_impacts_run
    ON news_exposure_position_impacts (run_id);

CREATE INDEX IF NOT EXISTS idx_news_articles_section
    ON news_articles (section);

CREATE INDEX IF NOT EXISTS idx_news_articles_normalized_url
    ON news_articles (normalized_url);

CREATE INDEX IF NOT EXISTS idx_news_articles_normalized_title
    ON news_articles (normalized_title);

CREATE INDEX IF NOT EXISTS idx_news_articles_content_hash
    ON news_articles (content_hash);

CREATE INDEX IF NOT EXISTS idx_news_articles_evidence_score
    ON news_articles (evidence_score);

CREATE INDEX IF NOT EXISTS idx_news_articles_recency_score
    ON news_articles (recency_score);

CREATE INDEX IF NOT EXISTS idx_industry_news_articles_industry_id
    ON industry_news_articles (industry_id);

CREATE INDEX IF NOT EXISTS idx_industry_news_articles_article_id
    ON industry_news_articles (article_id);

CREATE INDEX IF NOT EXISTS idx_company_news_articles_company_id
    ON company_news_articles (company_id);

CREATE INDEX IF NOT EXISTS idx_company_news_articles_article_id
    ON company_news_articles (article_id);

CREATE INDEX IF NOT EXISTS idx_sector_news_articles_sector_id
    ON sector_news_articles (sector_id);

CREATE INDEX IF NOT EXISTS idx_sector_news_articles_article_id
    ON sector_news_articles (article_id);

CREATE INDEX IF NOT EXISTS idx_us_news_articles_article_id
    ON us_news_articles (article_id);

CREATE INDEX IF NOT EXISTS idx_world_news_articles_article_id
    ON world_news_articles (article_id);

CREATE INDEX IF NOT EXISTS idx_world_news_sector_impacts_article_id
    ON world_news_sector_impacts (article_id);

CREATE INDEX IF NOT EXISTS idx_world_news_sector_impacts_sector_id
    ON world_news_sector_impacts (sector_id);

CREATE INDEX IF NOT EXISTS idx_world_news_sector_impacts_sector_article
    ON world_news_sector_impacts (sector_id, article_id);

CREATE INDEX IF NOT EXISTS idx_world_news_article_processing_article_id
    ON world_news_article_processing (article_id);

CREATE INDEX IF NOT EXISTS idx_us_news_sector_impacts_article_id
    ON us_news_sector_impacts (article_id);

CREATE INDEX IF NOT EXISTS idx_us_news_sector_impacts_sector_id
    ON us_news_sector_impacts (sector_id);

CREATE INDEX IF NOT EXISTS idx_us_news_sector_impacts_sector_article
    ON us_news_sector_impacts (sector_id, article_id);

CREATE INDEX IF NOT EXISTS idx_us_news_article_processing_article_id
    ON us_news_article_processing (article_id);

CREATE INDEX IF NOT EXISTS idx_industry_opportunist_impacts_article_id
    ON industry_opportunist_impacts (article_id);

CREATE INDEX IF NOT EXISTS idx_industry_opportunist_impacts_industry_id
    ON industry_opportunist_impacts (industry_id);

CREATE INDEX IF NOT EXISTS idx_industry_opportunist_impacts_industry_article
    ON industry_opportunist_impacts (industry_id, article_id);

CREATE INDEX IF NOT EXISTS idx_industry_opportunist_article_processing_article_id
    ON industry_opportunist_article_processing (article_id);

CREATE INDEX IF NOT EXISTS idx_company_opportunist_impacts_article_id
    ON company_opportunist_impacts (article_id);

CREATE INDEX IF NOT EXISTS idx_company_opportunist_impacts_company_id
    ON company_opportunist_impacts (company_id);

CREATE INDEX IF NOT EXISTS idx_company_opportunist_impacts_company_article
    ON company_opportunist_impacts (company_id, article_id);

CREATE INDEX IF NOT EXISTS idx_company_opportunist_article_processing_article_id
    ON company_opportunist_article_processing (article_id);

CREATE INDEX IF NOT EXISTS idx_company_opportunist_article_processing_company_id
    ON company_opportunist_article_processing (company_id);

CREATE INDEX IF NOT EXISTS idx_sector_opportunist_impacts_article_id
    ON sector_opportunist_impacts (article_id);

CREATE INDEX IF NOT EXISTS idx_sector_opportunist_impacts_sector_id
    ON sector_opportunist_impacts (sector_id);

CREATE INDEX IF NOT EXISTS idx_sector_opportunist_impacts_sector_article
    ON sector_opportunist_impacts (sector_id, article_id);

CREATE INDEX IF NOT EXISTS idx_sector_opportunist_article_processing_article_id
    ON sector_opportunist_article_processing (article_id);

CREATE INDEX IF NOT EXISTS idx_strategist_company_summaries_company_id
    ON strategist_company_summaries (company_id);

CREATE INDEX IF NOT EXISTS idx_strategist_runs_company_created
    ON strategist_runs (company_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_failed_urls_normalized_url
    ON failed_urls (normalized_url);

CREATE INDEX IF NOT EXISTS idx_failed_urls_stage
    ON failed_urls (stage);

CREATE INDEX IF NOT EXISTS idx_news_provenance_ticker_time ON news_articles(primary_ticker, event_time);
CREATE INDEX IF NOT EXISTS idx_news_provenance_catalyst ON news_articles(catalyst_id);

-- Time the title, summary or body last really changed. NULL for rows that predate this column.
CREATE TRIGGER IF NOT EXISTS news_articles_content_updated AFTER UPDATE OF title, summary ON news_articles
WHEN NEW.title IS NOT OLD.title OR NEW.summary IS NOT OLD.summary
BEGIN
    UPDATE news_articles SET content_updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') || '+00:00' WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS news_article_content_updated AFTER UPDATE OF body ON news_article_content
WHEN NEW.body IS NOT OLD.body
BEGIN
    UPDATE news_articles SET content_updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') || '+00:00' WHERE id = NEW.article_id;
END;

-- rowid is news_articles.id. Each trigger deletes and reinserts the row so the index follows edits.
CREATE VIRTUAL TABLE IF NOT EXISTS news_article_fts USING fts5(title, summary, body, tokenize='porter unicode61');

CREATE TRIGGER IF NOT EXISTS news_articles_fts_insert AFTER INSERT ON news_articles
BEGIN
    DELETE FROM news_article_fts WHERE rowid = NEW.id;
    INSERT INTO news_article_fts (rowid, title, summary, body)
    VALUES (NEW.id, NEW.title, NEW.summary, (SELECT body FROM news_article_content WHERE article_id = NEW.id));
END;

CREATE TRIGGER IF NOT EXISTS news_articles_fts_update AFTER UPDATE OF title, summary ON news_articles
WHEN NEW.title IS NOT OLD.title OR NEW.summary IS NOT OLD.summary
BEGIN
    DELETE FROM news_article_fts WHERE rowid = NEW.id;
    INSERT INTO news_article_fts (rowid, title, summary, body)
    VALUES (NEW.id, NEW.title, NEW.summary, (SELECT body FROM news_article_content WHERE article_id = NEW.id));
END;

CREATE TRIGGER IF NOT EXISTS news_articles_fts_delete AFTER DELETE ON news_articles
BEGIN
    DELETE FROM news_article_fts WHERE rowid = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS news_article_content_fts_insert AFTER INSERT ON news_article_content
BEGIN
    DELETE FROM news_article_fts WHERE rowid = NEW.article_id;
    INSERT INTO news_article_fts (rowid, title, summary, body)
    SELECT id, title, summary, NEW.body FROM news_articles WHERE id = NEW.article_id;
END;

CREATE TRIGGER IF NOT EXISTS news_article_content_fts_update AFTER UPDATE OF body ON news_article_content
WHEN NEW.body IS NOT OLD.body
BEGIN
    DELETE FROM news_article_fts WHERE rowid = NEW.article_id;
    INSERT INTO news_article_fts (rowid, title, summary, body)
    SELECT id, title, summary, NEW.body FROM news_articles WHERE id = NEW.article_id;
END;
