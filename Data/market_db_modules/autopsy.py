from __future__ import annotations

from contextlib import closing
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from db_common import get_connection, json_text
from .shared import normalize_timestamp


# v2 snapshots leave the manager input/output in manager_decision_payloads instead of
# embedding a second copy; readers get them from list_decision_autopsy_ledger.
SNAPSHOT_VERSION = "decision-autopsy-v2"


def move_payload_copies_out_of_ledger(conn: sqlite3.Connection) -> None:
    """Strip the manager input/output that v1 snapshots embedded.

    A snapshot copy is kept in manager_decision_payloads first if that decision has no
    payload of its own, so nothing is lost.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO manager_decision_payloads (
            manager_decision_history_id, manager_input_json, manager_output_json
        )
        SELECT
            ledger.manager_decision_history_id,
            json_extract(ledger.snapshot_json, '$.manager_input'),
            json_extract(ledger.snapshot_json, '$.manager_output')
        FROM decision_autopsy_ledger AS ledger
        JOIN manager_decision_history AS history ON history.id = ledger.manager_decision_history_id
        WHERE json_valid(ledger.snapshot_json)
          AND (
              json_extract(ledger.snapshot_json, '$.manager_input') <> '{}'
              OR json_extract(ledger.snapshot_json, '$.manager_output') <> '{}'
          )
        """
    )
    conn.execute(
        """
        UPDATE decision_autopsy_ledger
        SET snapshot_json = json_set(
            json_remove(snapshot_json, '$.manager_input', '$.manager_output'),
            '$.version', ?
        )
        WHERE json_valid(snapshot_json)
          AND (
              json_type(snapshot_json, '$.manager_input') IS NOT NULL
              OR json_type(snapshot_json, '$.manager_output') IS NOT NULL
          )
        """,
        (SNAPSHOT_VERSION,),
    )


def backfill_decision_autopsy_ledger(
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Create one snapshot for legacy manager decisions that predate the ledger.

    `record_manager_decision_history` writes the snapshot with each new decision, so
    only database setup needs to run this.
    """
    if conn is None:
        with closing(get_connection(db_path)) as local_conn, local_conn:
            return backfill_decision_autopsy_ledger(conn=local_conn)

    rows = conn.execute(
        """
        SELECT history.*
        FROM manager_decision_history AS history
        LEFT JOIN decision_autopsy_ledger AS ledger
            ON ledger.manager_decision_history_id = history.id
        WHERE ledger.id IS NULL
        """
    ).fetchall()
    for raw_row in rows:
        row = dict(raw_row)
        snapshot = {
            "version": SNAPSHOT_VERSION,
            "company_id": row.get("company_id"),
            "symbol": row.get("symbol"),
            "company_name": row.get("company_name"),
            "decision_run_at": row.get("decision_run_at"),
            "strategist_decision": row.get("strategist_decision"),
            "manager_decision": row.get("manager_decision"),
            "manager_confidence": row.get("manager_confidence"),
            "manager_reason": row.get("manager_reason"),
            "target_dte_bucket": row.get("target_dte_bucket"),
            "selected_option_id": row.get("selected_option_id"),
            "selected_option_symbol": row.get("selected_option_symbol"),
            "selected_expiration_date": row.get("selected_expiration_date"),
            "selected_strike_price": row.get("selected_strike_price"),
            "selected_option_source": row.get("selected_option_source"),
        }
        conn.execute(
            """
            INSERT INTO decision_autopsy_ledger (
                manager_decision_history_id, symbol, decision_run_at, snapshot_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row.get("symbol"),
                row.get("decision_run_at"),
                json_text(snapshot),
                row.get("created_at") or row.get("decision_run_at"),
            ),
        )
    return len(rows)


def list_decision_autopsy_ledger(
    *,
    company_id: int | None = None,
    symbol: str | None = None,
    resolved_only: bool = False,
    within_days: int | None = None,
    as_of: datetime | str | None = None,
    limit: int = 100,
    db_path: Path | str | None = None,
) -> list[sqlite3.Row]:
    where_clauses: list[str] = []
    parameters: list[Any] = []
    if company_id is not None:
        where_clauses.append("history.company_id = ?")
        parameters.append(int(company_id))
    elif str(symbol or "").strip():
        where_clauses.append("ledger.symbol = ?")
        parameters.append(str(symbol).strip().upper())
    as_of_timestamp = normalize_timestamp(as_of) if as_of is not None else None
    if resolved_only:
        where_clauses.extend(
            [
                "history.trade_executed = 1",
                "history.outcome_status = 'closed'",
                "history.realized_pnl_pct IS NOT NULL",
                "history.closed_at IS NOT NULL",
                "history.resolved_outcome_label IS NOT NULL",
            ]
        )
        if as_of_timestamp is not None:
            where_clauses.append("history.closed_at <= ?")
            parameters.append(as_of_timestamp)
    if within_days is not None and int(within_days) > 0:
        where_clauses.append("ledger.decision_run_at >= ?")
        reference_time = (
            datetime.fromisoformat(as_of_timestamp)
            if as_of_timestamp is not None
            else datetime.now(timezone.utc)
        )
        parameters.append((reference_time - timedelta(days=max(1, int(within_days)))).isoformat())
    if as_of_timestamp is not None:
        where_clauses.append("ledger.decision_run_at < ?")
        parameters.append(as_of_timestamp)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    with closing(get_connection(db_path)) as conn, conn:
        return conn.execute(
            f"""
            SELECT
                ledger.id,
                ledger.manager_decision_history_id,
                ledger.symbol,
                ledger.decision_run_at,
                ledger.snapshot_json,
                ledger.created_at,
                payload.manager_input_json,
                payload.manager_output_json,
                history.trade_executed,
                history.trade_execution_order_id,
                history.trade_execution_record_id,
                CASE WHEN history.outcome_status = 'closed' THEN history.realized_pnl_pct
                     ELSE history.latest_trade_pnl_pct END AS latest_trade_pnl_pct,
                history.realized_pnl_pct, history.closed_at, history.outcome_status,
                history.exit_reason, history.closing_order_ids_json,
                CASE WHEN history.outcome_status = 'closed' THEN history.closed_at
                     ELSE history.latest_trade_pnl_updated_at END AS latest_trade_pnl_updated_at,
                history.resolved_outcome_label,
                execution.paper AS execution_paper,
                execution.order_status AS execution_order_status,
                execution.option_symbol AS execution_option_symbol,
                execution.underlying_symbol AS execution_underlying_symbol,
                execution.order_qty AS execution_order_qty,
                execution.order_side AS execution_order_side,
                execution.submitted_at AS execution_submitted_at
            FROM decision_autopsy_ledger AS ledger
            LEFT JOIN manager_decision_history AS history
                ON history.id = ledger.manager_decision_history_id
            LEFT JOIN manager_decision_payloads AS payload
                ON payload.manager_decision_history_id = ledger.manager_decision_history_id
            LEFT JOIN option_trade_executions AS execution
                ON execution.id = history.trade_execution_record_id
                OR (
                    history.trade_execution_record_id IS NULL
                    AND execution.order_id = history.trade_execution_order_id
                )
            {where_sql}
            ORDER BY ledger.decision_run_at DESC, ledger.id DESC
            LIMIT ?
            """,
            (*parameters, max(1, int(limit))),
        ).fetchall()
