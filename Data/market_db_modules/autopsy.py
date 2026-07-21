from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from db_common import DB_PATH, get_connection, json_text


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        import json

        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def backfill_decision_autopsy_ledger(*, db_path: Path | str = DB_PATH) -> int:
    """Create one snapshot for legacy manager decisions that predate the ledger."""
    with get_connection(db_path) as conn:
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
                "version": "decision-autopsy-v1",
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
                "manager_input": _json_object(row.get("manager_input_json")),
                "manager_output": _json_object(row.get("manager_output_json")),
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
    limit: int = 100,
    db_path: Path | str = DB_PATH,
) -> list[sqlite3.Row]:
    where_clauses: list[str] = []
    parameters: list[Any] = []
    if company_id is not None:
        where_clauses.append("history.company_id = ?")
        parameters.append(int(company_id))
    elif str(symbol or "").strip():
        where_clauses.append("ledger.symbol = ?")
        parameters.append(str(symbol).strip().upper())
    if resolved_only:
        where_clauses.extend(
            [
                "history.trade_executed = 1",
                "history.latest_trade_pnl_pct IS NOT NULL",
                "history.resolved_outcome_label IS NOT NULL",
            ]
        )
    if within_days is not None and int(within_days) > 0:
        where_clauses.append("ledger.decision_run_at >= ?")
        parameters.append((datetime.now(timezone.utc) - timedelta(days=max(1, int(within_days)))).isoformat())

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    with get_connection(db_path) as conn:
        return conn.execute(
            f"""
            SELECT
                ledger.id,
                ledger.manager_decision_history_id,
                ledger.symbol,
                ledger.decision_run_at,
                ledger.snapshot_json,
                ledger.created_at,
                history.trade_executed,
                history.trade_execution_order_id,
                history.latest_trade_pnl_pct,
                history.latest_trade_pnl_updated_at,
                history.resolved_outcome_label
            FROM decision_autopsy_ledger AS ledger
            LEFT JOIN manager_decision_history AS history
                ON history.id = ledger.manager_decision_history_id
            {where_sql}
            ORDER BY ledger.decision_run_at DESC, ledger.id DESC
            LIMIT ?
            """,
            (*parameters, max(1, int(limit))),
        ).fetchall()
