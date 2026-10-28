"""Stored decision attributions: one row per manager decision, recomputed only while unsettled or out of date."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from db_common import get_connection
from services.decision_attribution import ATTRIBUTION_VERSION
from .bootstrap import initialize_database
from .evidence_thesis import latest_strategist_thesis_check

PENDING = 'pending'


def _unsettled(table: str) -> str:
    """SQL for a stored row that may still change: pending, with a horizon whose closes have not arrived yet, or
    still inside the window where late closes may replace an `unavailable` horizon."""
    return (f"({table}.primary_failure = '{PENDING}' OR EXISTS (SELECT 1 FROM json_each({table}.record_json, "
            f"'$.direction.horizon_outcomes') WHERE value = '{PENDING}') "
            f"OR json_extract({table}.record_json, '$.settled') = 0)")


def upsert_attribution(record: dict[str, Any], *, db_path: Path | str | None = None) -> None:
    """Insert the attribution, or replace the stored one only while it is unsettled or from another version.

    Another version counts as stale (`!=`, not "older"): versions are opaque strings.
    """
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute(
            f"""
            INSERT INTO decision_attributions (
                manager_decision_history_id, attribution_version, evidence_quality, evidence_source,
                direction_outcome, expression_outcome, primary_failure, record_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(manager_decision_history_id) DO UPDATE SET
                attribution_version = excluded.attribution_version, evidence_quality = excluded.evidence_quality,
                evidence_source = excluded.evidence_source, direction_outcome = excluded.direction_outcome,
                expression_outcome = excluded.expression_outcome, primary_failure = excluded.primary_failure,
                record_json = excluded.record_json, computed_at = excluded.computed_at
            WHERE {_unsettled('decision_attributions')}
               OR decision_attributions.attribution_version != excluded.attribution_version
            """,
            (record['manager_decision_history_id'], record['attribution_version'], record['evidence']['quality'],
             record['evidence']['source'], record['direction']['outcome'], record['expression']['outcome'],
             record['primary_failure'], json.dumps(record), datetime.now(timezone.utc).isoformat()),
        )


def list_attributions(*, since: str | None = None, db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Stored attributions computed at or after `since`, each with its parsed `record`."""
    initialize_database(db_path=db_path)
    query = "SELECT * FROM decision_attributions"
    parameters: list[Any] = []
    if since:
        query += " WHERE julianday(computed_at) >= julianday(?)"
        parameters.append(since)
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute(query + " ORDER BY manager_decision_history_id", parameters).fetchall()
    return [{**{key: row[key] for key in row.keys() if key != 'record_json'}, 'record': json.loads(row['record_json'])}
            for row in rows]


def decisions_needing_attribution(limit: int, *, db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Decisions with no attribution (oldest first), then unsettled or out-of-version ones (least recently computed first).

    Each row is the history row with its frozen manager input/output text (as `llm_replay_store` lists them), the
    candidate's forward residual return, its primary catalyst state and its Phase 4 thesis check; None where absent.
    """
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn:
        rows = [dict(row) for row in conn.execute(
            f"""
            SELECT history.*, payload.manager_input_json, payload.manager_output_json,
                   observation.forward_residual_return, catalyst.state_json AS catalyst_state_json
            FROM manager_decision_history AS history
            JOIN manager_decision_payloads AS payload ON payload.manager_decision_history_id = history.id
            LEFT JOIN decision_attributions AS stored ON stored.manager_decision_history_id = history.id
            LEFT JOIN candidate_price_observations AS observation ON observation.candidate_id = history.candidate_id
            LEFT JOIN candidate_catalysts AS catalyst
                   ON catalyst.candidate_id = history.candidate_id AND catalyst.is_primary = 1
            WHERE stored.manager_decision_history_id IS NULL OR {_unsettled('stored')}
               OR stored.attribution_version != ?
            ORDER BY stored.manager_decision_history_id IS NOT NULL, stored.computed_at, history.decision_run_at, history.id
            LIMIT ?
            """,
            (ATTRIBUTION_VERSION, max(1, int(limit))),
        )]
    for row in rows:
        state = row.pop('catalyst_state_json')
        row['catalyst_state'] = json.loads(state) if state else None
        row['thesis_check'] = latest_strategist_thesis_check(row['candidate_id'], db_path=db_path)
    return rows
