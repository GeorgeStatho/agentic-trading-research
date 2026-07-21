from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from db_common import DB_PATH, get_connection, json_text
from .bootstrap import initialize_database
from .shared import normalize_timestamp


def list_replayable_manager_decisions(
    *,
    limit: int = 100,
    db_path: Path | str = DB_PATH,
) -> list[sqlite3.Row]:
    """Return resolved, executed decisions in chronological replay order."""
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        return conn.execute(
            """
            SELECT *
            FROM (
                SELECT
                    history.id,
                    history.company_id,
                    history.symbol,
                    history.company_name,
                    history.decision_run_at,
                    history.manager_decision,
                    history.manager_confidence,
                    history.manager_input_json,
                    history.manager_output_json,
                    history.trade_execution_order_id,
                    history.trade_execution_record_id,
                    history.latest_trade_pnl_pct,
                    history.latest_trade_pnl_updated_at,
                    history.resolved_outcome_label,
                    execution.paper AS execution_paper,
                    execution.order_status AS execution_order_status,
                    execution.option_symbol AS execution_option_symbol,
                    execution.underlying_symbol AS execution_underlying_symbol,
                    execution.order_qty AS execution_order_qty,
                    execution.order_side AS execution_order_side,
                    execution.submitted_at AS execution_submitted_at
                FROM manager_decision_history AS history
                LEFT JOIN option_trade_executions AS execution
                  ON execution.id = history.trade_execution_record_id
                  OR (
                      history.trade_execution_record_id IS NULL
                      AND execution.order_id = history.trade_execution_order_id
                  )
                WHERE history.trade_executed = 1
                  AND history.latest_trade_pnl_pct IS NOT NULL
                  AND history.resolved_outcome_label IS NOT NULL
                ORDER BY history.decision_run_at DESC, history.id DESC
                LIMIT ?
            )
            ORDER BY decision_run_at ASC, id ASC
            """,
            (max(1, int(limit)),),
        ).fetchall()


def create_manager_replay_evaluation(
    *,
    model: str,
    prompt_version: str,
    autopsy_limit: int,
    autopsy_max_age_days: int,
    started_at: Any = None,
    db_path: Path | str = DB_PATH,
) -> int:
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO manager_replay_evaluations (
                model, prompt_version, autopsy_limit, autopsy_max_age_days, started_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(model or ""),
                str(prompt_version or ""),
                max(1, int(autopsy_limit)),
                max(1, int(autopsy_max_age_days)),
                normalize_timestamp(started_at),
            ),
        )
        return int(cursor.lastrowid)


def record_manager_replay_result(
    *,
    evaluation_id: int,
    manager_decision_history_id: int,
    replay_mode: str,
    decision_run_at: Any,
    autopsy_ids: list[int],
    recommendation: dict[str, Any] | None = None,
    error_text: str | None = None,
    db_path: Path | str = DB_PATH,
) -> None:
    if replay_mode not in {
        "baseline",
        "resolved_autopsy_feedback",
        "historical_outcome_audit",
        "legacy_partial",
    }:
        raise ValueError(f"Unsupported manager replay mode: {replay_mode}")
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO manager_replay_results (
                evaluation_id, manager_decision_history_id, replay_mode, decision_run_at,
                autopsy_ids_json, recommendation_json, error_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(evaluation_id),
                int(manager_decision_history_id),
                replay_mode,
                normalize_timestamp(decision_run_at),
                json_text([int(value) for value in autopsy_ids if int(value) > 0]) or "[]",
                json_text(recommendation) if isinstance(recommendation, dict) else None,
                str(error_text or "").strip() or None,
            ),
        )


def complete_manager_replay_evaluation(
    *,
    evaluation_id: int,
    source_decision_count: int,
    replayed_decision_count: int,
    skipped_decision_count: int,
    metrics: dict[str, Any],
    completed_at: Any = None,
    db_path: Path | str = DB_PATH,
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE manager_replay_evaluations
            SET source_decision_count = ?, replayed_decision_count = ?, skipped_decision_count = ?,
                metrics_json = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                max(0, int(source_decision_count)),
                max(0, int(replayed_decision_count)),
                max(0, int(skipped_decision_count)),
                json_text(metrics) or "{}",
                normalize_timestamp(completed_at),
                int(evaluation_id),
            ),
        )
