from __future__ import annotations

from pathlib import Path
from typing import Any

from db_common import DB_PATH, clean_text, get_connection, json_text
from .bootstrap import initialize_database


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
            country, currency, category, importance, actual, forecast, previous,
            source_url, raw_json
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
