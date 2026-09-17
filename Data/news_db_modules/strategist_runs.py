from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db_common import DB_PATH, get_connection, json_text


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
    db_path: Path | str = DB_PATH,
    conn=None,
) -> int:
    if conn is None:
        with get_connection(db_path) as local_conn:
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
    return int(cursor.lastrowid)
