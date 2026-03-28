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
    summary TEXT,
    body TEXT,
    published_at TEXT,
    section TEXT,
    source_url TEXT NOT NULL,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_macro_events_event_date
    ON macro_events (event_date);

CREATE INDEX IF NOT EXISTS idx_macro_events_category
    ON macro_events (category);

CREATE INDEX IF NOT EXISTS idx_news_articles_published_at
    ON news_articles (published_at);

CREATE INDEX IF NOT EXISTS idx_news_articles_section
    ON news_articles (section);
