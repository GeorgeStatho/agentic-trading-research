from __future__ import annotations

from contextlib import closing
import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from db_common import clean_text, get_connection, json_text
from .bootstrap import initialize_database
from .shared import coerce_int, normalize_timestamp


class TelemetryNetworkCapReached(Exception):
    """The network already added its allowance of new install IDs today."""


def _admit_new_install(conn: sqlite3.Connection, client_ip: str, now: datetime, limit: int) -> None:
    day = now.astimezone(timezone.utc).date().isoformat()
    conn.execute("DELETE FROM telemetry_daily_salts WHERE day < ?", (day,))
    conn.execute("DELETE FROM telemetry_network_days WHERE day < ?", (day,))
    conn.execute("INSERT OR IGNORE INTO telemetry_daily_salts (day, salt) VALUES (?, ?)", (day, secrets.token_hex(16)))
    salt = conn.execute("SELECT salt FROM telemetry_daily_salts WHERE day = ?", (day,)).fetchone()["salt"]
    network = hmac.new(salt.encode(), client_ip.encode(), hashlib.sha256).hexdigest()
    row = conn.execute(
        "SELECT new_install_count FROM telemetry_network_days WHERE day = ? AND network_hash = ?", (day, network)
    ).fetchone()
    if row is not None and row["new_install_count"] >= limit:
        raise TelemetryNetworkCapReached(f"This network already added {limit} new installs today.")
    conn.execute(
        """
        INSERT INTO telemetry_network_days (day, network_hash, new_install_count) VALUES (?, ?, 1)
        ON CONFLICT(day, network_hash) DO UPDATE SET new_install_count = new_install_count + 1
        """,
        (day, network),
    )


def record_telemetry_installation(
    payload: dict[str, Any],
    *,
    client_ip: str | None = None,
    now: datetime | None = None,
    new_installs_per_network: int = 3,
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
            return record_telemetry_installation(
                payload, client_ip=client_ip, now=now, new_installs_per_network=new_installs_per_network,
                db_path=db_path, conn=local_conn,
            )

    now = now or datetime.now(timezone.utc)
    known = conn.execute("SELECT 1 FROM telemetry_installations WHERE install_id = ?", (install_id,)).fetchone()
    if client_ip and known is None:
        _admit_new_install(conn, client_ip, now, new_installs_per_network)

    project = clean_text(payload.get("project"))
    runtime = clean_text(payload.get("runtime"))
    release = clean_text(payload.get("release"))
    install_created_at = (
        normalize_timestamp(payload.get("install_created_at"))
        if payload.get("install_created_at") not in (None, "")
        else None
    )
    started_at = normalize_timestamp(payload.get("started_at"))
    # Seen times come from this server's clock: the client's own start time can't make an install look older.
    seen_at = normalize_timestamp(now)
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
        seen_at,
        seen_at,
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
            SUM(CASE WHEN first_seen_at >= ? THEN 1 ELSE 0 END) AS unique_installs_last_30d,
            SUM(CASE WHEN date(last_seen_at) > date(first_seen_at) AND last_seen_at >= ? THEN 1 ELSE 0 END)
                AS active_install_count
        FROM telemetry_installations
    """
    cutoff_7d = normalize_timestamp(datetime.now(timezone.utc) - timedelta(days=7))
    cutoff_30d = normalize_timestamp(datetime.now(timezone.utc) - timedelta(days=30))
    parameters: list[Any] = [cutoff_7d, cutoff_30d, cutoff_30d]
    if normalized_project is not None:
        query += " WHERE project = ?"
        parameters.append(normalized_project)

    with closing(get_connection(db_path)) as conn, conn:
        row = conn.execute(query, tuple(parameters)).fetchone()

    return {
        "project": normalized_project or "",
        "active_install_count": int(row["active_install_count"] or 0),
        "unique_install_count": int(row["unique_install_count"] or 0),
        "startup_ping_count": int(row["startup_ping_count"] or 0),
        "unique_installs_last_7d": int(row["unique_installs_last_7d"] or 0),
        "unique_installs_last_30d": int(row["unique_installs_last_30d"] or 0),
        "last_seen_at": str(row["last_seen_at"] or "").strip(),
        "last_started_at": str(row["last_started_at"] or "").strip(),
    }
