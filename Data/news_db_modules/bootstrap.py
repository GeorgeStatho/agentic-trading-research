from __future__ import annotations

from contextlib import closing
from functools import cache
import sqlite3
from pathlib import Path

from db_common import (DATA_DIR, applied_setup_fingerprint, get_connection, initialize_schema,
                       lock_for_migration, record_setup_fingerprint, setup_fingerprint, table_columns,
                       table_exists)
from .shared import MACRO_EVENT_COLUMN_TYPES, NEWS_ARTICLE_COLUMN_TYPES, SCHEMA_PATH
from catalyst_schema import migrate_catalysts


SETUP_NAME = "news"


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


def _move_article_content(conn: sqlite3.Connection) -> None:
    """Move the body and raw_json that older code kept on news_articles into their own table."""
    lock_for_migration(conn)
    if "body" not in table_columns(conn, "news_articles"):
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO news_article_content (article_id, body, raw_json)
        SELECT id, body, raw_json
        FROM news_articles
        WHERE body IS NOT NULL OR raw_json IS NOT NULL
        """
    )
    conn.execute("ALTER TABLE news_articles DROP COLUMN body")
    conn.execute("ALTER TABLE news_articles DROP COLUMN raw_json")


def _run_bootstrap_migrations(conn: sqlite3.Connection) -> None:
    _migrate_company_opportunist_article_processing(conn)
    _ensure_missing_columns(conn, "macro_events", MACRO_EVENT_COLUMN_TYPES)
    _ensure_missing_columns(conn, "news_articles", NEWS_ARTICLE_COLUMN_TYPES)
    _ensure_missing_columns(conn, "strategist_runs", {"candidate_id": "TEXT"})


@cache
def _setup_fingerprint(schema_path: Path) -> str:
    # Data/*.py and Data/*.sql cover db_common and the catalyst schema this setup also runs.
    return setup_fingerprint([*DATA_DIR.glob("*.py"), *DATA_DIR.glob("*.sql"),
                              *Path(__file__).parent.glob("*.py"), schema_path])


def initialize_database(
    db_path: Path | str | None = None,
    schema_path: Path | str = SCHEMA_PATH,
) -> None:
    """Create and migrate the news schema.

    The agents and scrapers call this once per payload or batch, so the work runs only when
    the database was last set up by different setup files; otherwise it is one lookup.
    """
    fingerprint = _setup_fingerprint(Path(schema_path).resolve())
    with closing(get_connection(db_path)) as conn:
        if applied_setup_fingerprint(conn, SETUP_NAME) == fingerprint:
            return

    with closing(get_connection(db_path)) as conn, conn:
        _run_bootstrap_migrations(conn)

    initialize_schema(db_path, schema_path)

    with closing(get_connection(db_path)) as conn, conn:
        _run_bootstrap_migrations(conn)
        _move_article_content(conn)
        migrate_catalysts(conn)
        record_setup_fingerprint(conn, SETUP_NAME, fingerprint)
