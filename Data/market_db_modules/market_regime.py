from __future__ import annotations

from pathlib import Path
from typing import Any

from db_common import DB_PATH, get_connection, json_text
from .bootstrap import initialize_database


def record_market_regime_snapshot(snapshot: dict[str, Any], db_path: Path | str = DB_PATH) -> int:
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO market_regime_runs (as_of, state, confidence, policy_version, snapshot_json)
            VALUES (?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                str(snapshot.get("as_of") or ""),
                str(snapshot.get("state") or "unknown"),
                str(snapshot.get("confidence") or "low"),
                str(snapshot.get("policy_version") or "market-regime-v1"),
                json_text(snapshot),
            ),
        )
        return int(cursor.fetchone()["id"])


def get_latest_market_regime_snapshot(db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT id, as_of, state, confidence, policy_version, snapshot_json, created_at FROM market_regime_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    import json

    record = dict(row)
    try:
        snapshot = json.loads(record.pop("snapshot_json") or "{}")
    except json.JSONDecodeError:
        snapshot = {}
    return {**record, **snapshot, "market_regime_run_id": record["id"]}
