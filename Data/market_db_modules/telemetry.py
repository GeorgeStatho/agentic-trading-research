from __future__ import annotations

from contextlib import closing
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from db_common import clean_text, get_connection, json_text
from .bootstrap import initialize_database
from .shared import coerce_int, normalize_timestamp


def record_telemetry_installation(
    payload: dict[str, Any],
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    install_id = clean_text(payload.get("install_id"))
    if install_id is None:
        return None

    if conn is None:
        initialize_database(db_path=db_path)
        with closing(get_connection(db_path)) as local_conn, local_conn:
            return record_telemetry_installation(payload, db_path=db_path, conn=local_conn)

    project = clean_text(payload.get("project"))
    runtime = clean_text(payload.get("runtime"))
    release = clean_text(payload.get("release"))
    install_created_at = (
        normalize_timestamp(payload.get("install_created_at"))
        if payload.get("install_created_at") not in (None, "")
        else None
    )
    started_at = normalize_timestamp(payload.get("started_at"))
    telemetry_version = coerce_int(payload.get("telemetry_version"))

    values = (
        install_id,
        project,
        telemetry_version,
        install_created_at,
        runtime,
        runtime,
        release,
        release,
        started_at,
        started_at,
        started_at,
        1,
        json_text(payload),
    )
    row = conn.execute(
        """
        INSERT INTO telemetry_installations (
            install_id,
            project,
            telemetry_version,
            install_created_at,
            first_runtime,
            last_runtime,
            first_release,
            last_release,
            first_seen_at,
            last_seen_at,
            last_started_at,
            startup_ping_count,
            raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(install_id) DO UPDATE SET
            project = COALESCE(excluded.project, telemetry_installations.project),
            telemetry_version = COALESCE(excluded.telemetry_version, telemetry_installations.telemetry_version),
            install_created_at = COALESCE(excluded.install_created_at, telemetry_installations.install_created_at),
            last_runtime = COALESCE(excluded.last_runtime, telemetry_installations.last_runtime),
            last_release = COALESCE(excluded.last_release, telemetry_installations.last_release),
            last_seen_at = excluded.last_seen_at,
            last_started_at = excluded.last_started_at,
            startup_ping_count = telemetry_installations.startup_ping_count + 1,
            raw_json = excluded.raw_json
        RETURNING
            id,
            install_id,
            project,
            first_runtime,
            last_runtime,
            first_seen_at,
            last_seen_at,
            last_started_at,
            startup_ping_count
        """,
        values,
    ).fetchone()
    return dict(row) if row is not None else None


def get_telemetry_summary(
    *,
    project: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    initialize_database(db_path=db_path)
    normalized_project = clean_text(project)
    query = """
        SELECT
            COUNT(*) AS unique_install_count,
            COALESCE(SUM(startup_ping_count), 0) AS startup_ping_count,
            MAX(last_seen_at) AS last_seen_at,
            MAX(last_started_at) AS last_started_at,
            SUM(CASE WHEN first_seen_at >= ? THEN 1 ELSE 0 END) AS unique_installs_last_7d,
            SUM(CASE WHEN first_seen_at >= ? THEN 1 ELSE 0 END) AS unique_installs_last_30d
        FROM telemetry_installations
    """
    cutoff_7d = normalize_timestamp(datetime.now(timezone.utc) - timedelta(days=7))
    cutoff_30d = normalize_timestamp(datetime.now(timezone.utc) - timedelta(days=30))
    parameters: list[Any] = [cutoff_7d, cutoff_30d]
    if normalized_project is not None:
        query += " WHERE project = ?"
        parameters.append(normalized_project)

    with closing(get_connection(db_path)) as conn, conn:
        row = conn.execute(query, tuple(parameters)).fetchone()

    return {
        "project": normalized_project or "",
        "unique_install_count": int(row["unique_install_count"] or 0),
        "startup_ping_count": int(row["startup_ping_count"] or 0),
        "unique_installs_last_7d": int(row["unique_installs_last_7d"] or 0),
        "unique_installs_last_30d": int(row["unique_installs_last_30d"] or 0),
        "last_seen_at": str(row["last_seen_at"] or "").strip(),
        "last_started_at": str(row["last_started_at"] or "").strip(),
    }
