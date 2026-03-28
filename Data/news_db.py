from __future__ import annotations

from pathlib import Path
from typing import Any

from db_common import DB_PATH, DATA_DIR, clean_text, get_connection, initialize_schema, json_text, table_columns, table_exists


SCHEMA_PATH = DATA_DIR / "news_schema.sql"

MACRO_EVENT_COLUMN_TYPES = {
    "currency": "TEXT",
    "actual": "TEXT",
    "forecast": "TEXT",
    "previous": "TEXT",
}


def initialize_database(db_path: Path | str = DB_PATH, schema_path: Path | str = SCHEMA_PATH) -> None:
    initialize_schema(db_path, schema_path)
    with get_connection(db_path) as conn:
        if table_exists(conn, "macro_events"):
            existing_columns = table_columns(conn, "macro_events")
            for column_name, column_type in MACRO_EVENT_COLUMN_TYPES.items():
                if column_name not in existing_columns:
                    conn.execute(
                        f"ALTER TABLE macro_events ADD COLUMN {column_name} {column_type}"
                    )


def add_macro_event(
    source: str,
    event_key: str,
    event_name: str,
    event_date: str | None = None,
    event_time: str | None = None,
    country: str | None = None,
    currency: str | None = None,
    category: str | None = None,
    importance: str | None = None,
    actual: str | None = None,
    forecast: str | None = None,
    previous: str | None = None,
    source_url: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    values = (
        source,
        event_key,
        event_name,
        event_date,
        event_time,
        country,
        currency,
        category,
        importance,
        actual,
        forecast,
        previous,
        source_url,
        json_text(raw_json),
    )
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_macro_event(
                source,
                event_key,
                event_name,
                event_date=event_date,
                event_time=event_time,
                country=country,
                currency=currency,
                category=category,
                importance=importance,
                actual=actual,
                forecast=forecast,
                previous=previous,
                source_url=source_url,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO macro_events (
            source, event_key, event_name, event_date, event_time,
            country, currency, category, importance, actual, forecast, previous, source_url, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_key) DO UPDATE SET
            source = excluded.source,
            event_name = excluded.event_name,
            event_date = excluded.event_date,
            event_time = excluded.event_time,
            country = excluded.country,
            currency = excluded.currency,
            category = excluded.category,
            importance = excluded.importance,
            actual = excluded.actual,
            forecast = excluded.forecast,
            previous = excluded.previous,
            source_url = excluded.source_url,
            raw_json = excluded.raw_json
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def add_news_article(
    source: str,
    article_key: str,
    title: str,
    source_url: str,
    summary: str | None = None,
    body: str | None = None,
    published_at: str | None = None,
    section: str | None = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    values = (
        source,
        article_key,
        title,
        summary,
        body,
        published_at,
        section,
        source_url,
        json_text(raw_json),
    )
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_news_article(
                source,
                article_key,
                title,
                source_url,
                summary=summary,
                body=body,
                published_at=published_at,
                section=section,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO news_articles (
            source, article_key, title, summary, body,
            published_at, section, source_url, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(article_key) DO UPDATE SET
            source = excluded.source,
            title = excluded.title,
            summary = excluded.summary,
            body = excluded.body,
            published_at = excluded.published_at,
            section = excluded.section,
            source_url = excluded.source_url,
            raw_json = excluded.raw_json
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def load_macro_events(
    events: list[dict[str, Any]],
    db_path: Path | str = DB_PATH,
) -> None:
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        for event in events:
            event_key = clean_text(event.get("event_key"))
            event_name = clean_text(event.get("event_name"))
            if event_key is None or event_name is None:
                continue

            add_macro_event(
                source=clean_text(event.get("source")) or "unknown",
                event_key=event_key,
                event_name=event_name,
                event_date=clean_text(event.get("event_date")),
                event_time=clean_text(event.get("event_time")),
                country=clean_text(event.get("country")),
                currency=clean_text(event.get("currency")),
                category=clean_text(event.get("category")),
                importance=clean_text(event.get("importance")),
                actual=clean_text(event.get("actual")),
                forecast=clean_text(event.get("forecast")),
                previous=clean_text(event.get("previous")),
                source_url=clean_text(event.get("source_url")),
                raw_json=event,
                conn=conn,
            )


def load_news_articles(
    articles: list[dict[str, Any]],
    db_path: Path | str = DB_PATH,
) -> None:
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        for article in articles:
            source = clean_text(article.get("source")) or "unknown"
            article_key = clean_text(article.get("article_key")) or clean_text(article.get("source_url"))
            title = clean_text(article.get("title")) or article_key
            source_url = clean_text(article.get("source_url"))
            if article_key is None or title is None or source_url is None:
                continue

            add_news_article(
                source=source,
                article_key=article_key,
                title=title,
                source_url=source_url,
                summary=clean_text(article.get("summary")),
                body=clean_text(article.get("body")),
                published_at=clean_text(article.get("published_at")),
                section=clean_text(article.get("section")),
                raw_json=article,
                conn=conn,
            )


if __name__ == "__main__":
    initialize_database()
    print(f"Initialized news database schema in {DB_PATH}")
