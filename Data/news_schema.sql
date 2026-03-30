PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS macro_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    event_key TEXT NOT NULL UNIQUE,
    event_name TEXT NOT NULL,
    event_date TEXT,
    event_time TEXT,
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
    summary TEXT,
    body TEXT,
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
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

CREATE INDEX IF NOT EXISTS idx_macro_events_event_date
    ON macro_events (event_date);

CREATE INDEX IF NOT EXISTS idx_macro_events_category
    ON macro_events (category);

CREATE INDEX IF NOT EXISTS idx_news_articles_published_at
    ON news_articles (published_at);

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

CREATE INDEX IF NOT EXISTS idx_failed_urls_normalized_url
    ON failed_urls (normalized_url);

CREATE INDEX IF NOT EXISTS idx_failed_urls_stage
    ON failed_urls (stage);
