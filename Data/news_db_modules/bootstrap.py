from __future__ import annotations

import sqlite3
from pathlib import Path

from db_common import DB_PATH, get_connection, initialize_schema, table_columns, table_exists
from .shared import MACRO_EVENT_COLUMN_TYPES, NEWS_ARTICLE_COLUMN_TYPES, SCHEMA_PATH


def _ensure_missing_columns(
    conn: sqlite3.Connection,
    table_name: str,
    expected_columns: dict[str, str],
) -> None:
    if not table_exists(conn, table_name):
        return

    existing_columns = table_columns(conn, table_name)
    for column_name, column_type in expected_columns.items():
        if column_name not in existing_columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _migrate_company_opportunist_article_processing(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "company_opportunist_article_processing"):
        return

    if "company_id" in table_columns(conn, "company_opportunist_article_processing"):
        return

    conn.executescript(
        """
        CREATE TABLE company_opportunist_article_processing_new (
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

        INSERT INTO company_opportunist_article_processing_new (
            article_id, company_id, model, raw_json, processed_at
        )
        SELECT
            cop.article_id,
            coi.company_id,
            cop.model,
            cop.raw_json,
            cop.processed_at
        FROM company_opportunist_article_processing AS cop
        JOIN company_opportunist_impacts AS coi ON coi.article_id = cop.article_id
        GROUP BY cop.article_id, coi.company_id;

        DROP TABLE company_opportunist_article_processing;
        ALTER TABLE company_opportunist_article_processing_new
        RENAME TO company_opportunist_article_processing;
        """
    )


def _run_bootstrap_migrations(conn: sqlite3.Connection) -> None:
    _migrate_company_opportunist_article_processing(conn)
    _ensure_missing_columns(conn, "macro_events", MACRO_EVENT_COLUMN_TYPES)
    _ensure_missing_columns(conn, "news_articles", NEWS_ARTICLE_COLUMN_TYPES)
    _ensure_missing_columns(conn, "strategist_runs", {"candidate_id": "TEXT"})


def initialize_database(
    db_path: Path | str = DB_PATH,
    schema_path: Path | str = SCHEMA_PATH,
) -> None:
    with get_connection(db_path) as conn:
        _run_bootstrap_migrations(conn)

    initialize_schema(db_path, schema_path)

    with get_connection(db_path) as conn:
        _run_bootstrap_migrations(conn)
        from catalyst_schema import migrate_catalysts
        migrate_catalysts(conn)
