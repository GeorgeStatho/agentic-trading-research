from __future__ import annotations

from contextlib import closing
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db_common import get_connection, json_text
from catalyst_schema import stamp_candidate_catalyst


def record_strategist_run(
    *,
    company_id: int,
    model: str,
    prompt_version: str,
    input_article_ids: list[int],
    input_payload: dict[str, Any],
    output_payload: dict[str, Any] | None,
    status: str,
    error_message: str | None = None,
    created_at: str | None = None,
    db_path: Path | str | None = None,
    conn=None,
) -> int:
    if conn is None:
        with closing(get_connection(db_path)) as local_conn, local_conn:
            return record_strategist_run(
                company_id=company_id,
                model=model,
                prompt_version=prompt_version,
                input_article_ids=input_article_ids,
                input_payload=input_payload,
                output_payload=output_payload,
                status=status,
                error_message=error_message,
                created_at=created_at,
                conn=local_conn,
            )

    created_at = created_at or datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO strategist_runs (
            company_id, model, prompt_version, input_article_ids_json, input_payload_json,
            output_payload_json, status, error_message, created_at, candidate_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            company_id,
            model,
            prompt_version,
            json_text(input_article_ids),
            json_text(input_payload),
            json_text(output_payload),
            status,
            error_message,
            created_at,
            input_payload.get('candidate_id'),
        ),
    )
    record_id = int(cursor.lastrowid)
    stamp_candidate_catalyst(conn, 'strategist_runs', record_id, input_payload.get('candidate_id'))
    return record_id


def latest_answered_strategist_run(company_id: int, *, db_path: Path | str | None = None) -> dict[str, Any] | None:
    """The company's latest completed run answered by the model, never one that reused an earlier answer."""
    with closing(get_connection(db_path)) as conn:
        row = conn.execute(
            """
            SELECT id, model, prompt_version, input_payload_json, output_payload_json, created_at
            FROM strategist_runs
            WHERE company_id = ? AND status = 'completed'
              AND json_extract(output_payload_json, '$.reused_from_run_id') IS NULL
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (company_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "model": row["model"],
        "prompt_version": row["prompt_version"],
        "input_payload": json.loads(row["input_payload_json"] or "{}"),
        "output_payload": json.loads(row["output_payload_json"] or "{}"),
        "created_at": row["created_at"],
    }
