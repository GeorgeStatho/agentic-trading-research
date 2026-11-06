from __future__ import annotations

from contextlib import closing
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from db_common import clean_text, get_connection, json_text
from .autopsy import SNAPSHOT_VERSION
from .bootstrap import initialize_database
from .shared import coerce_int, find_company_row_by_symbol, normalize_bool_to_int, normalize_timestamp
from .backtesting import ensure_decision_evidence_snapshot
from .position_outcomes import close_position_outcome
from .shadow_gates import record_decision_gate_verdicts
from catalyst_schema import stamp_candidate_catalyst
from services.core.numbers import safe_float


def record_manager_decision_history(
    history: dict[str, Any],
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int | None:
    if not isinstance(history, dict):
        return None

    if conn is None:
        initialize_database(db_path=db_path)
        with closing(get_connection(db_path)) as local_conn, local_conn:
            return record_manager_decision_history(history, conn=local_conn)

    company_id = coerce_int(history.get("company_id"))
    symbol = clean_text(history.get("symbol"))
    company_name = clean_text(history.get("company_name"))
    if company_id is None and symbol is not None:
        company_row = find_company_row_by_symbol(symbol, conn=conn)
        if company_row is not None:
            company_id = coerce_int(company_row["id"])
            symbol = symbol or clean_text(company_row["symbol"])
            company_name = company_name or clean_text(company_row["name"])

    decision_run_at = normalize_timestamp(
        history.get("decision_run_at")
        or history.get("ran_at")
        or history.get("created_at")
    )
    values = (
        company_id,
        symbol,
        company_name,
        decision_run_at,
        clean_text(history.get("manager_stage_version")),
        clean_text(history.get("strategist_decision")),
        clean_text(history.get("manager_decision")),
        clean_text(history.get("manager_confidence")),
        clean_text(history.get("manager_reason")),
        clean_text(history.get("target_dte_bucket")),
        clean_text(history.get("selected_option_id")),
        clean_text(history.get("selected_option_symbol")),
        clean_text(history.get("selected_expiration_date")),
        safe_float(history.get("selected_strike_price")),
        clean_text(history.get("selected_option_source")),
        normalize_bool_to_int(history.get("trade_executed")),
        clean_text(history.get("trade_execution_order_id")),
        coerce_int(history.get("trade_execution_record_id")),
        safe_float(history.get("latest_trade_pnl_pct")),
        (
            normalize_timestamp(history.get("latest_trade_pnl_updated_at"))
            if history.get("latest_trade_pnl_updated_at") not in (None, "")
            else None
        ),
        (
            normalize_timestamp(history.get("pnl_expires_at"))
            if history.get("pnl_expires_at") not in (None, "")
            else None
        ),
        clean_text(history.get("resolved_outcome_label")),
        clean_text(history.get("outcome_status")),
        safe_float(history.get("realized_pnl_pct")),
        (
            normalize_timestamp(history.get("closed_at"))
            if history.get("closed_at") not in (None, "")
            else None
        ),
        decision_run_at,
        decision_run_at,
    )
    cursor = conn.execute(
        """
        INSERT INTO manager_decision_history (
            company_id,
            symbol,
            company_name,
            decision_run_at,
            manager_stage_version,
            strategist_decision,
            manager_decision,
            manager_confidence,
            manager_reason,
            target_dte_bucket,
            selected_option_id,
            selected_option_symbol,
            selected_expiration_date,
            selected_strike_price,
            selected_option_source,
            trade_executed,
            trade_execution_order_id,
            trade_execution_record_id,
            latest_trade_pnl_pct,
            latest_trade_pnl_updated_at,
            pnl_expires_at,
            resolved_outcome_label,
            outcome_status,
            realized_pnl_pct,
            closed_at,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )
    decision_history_id = int(cursor.lastrowid)
    conn.execute(
        """
        INSERT INTO manager_decision_payloads (
            manager_decision_history_id, manager_input_json, manager_output_json
        ) VALUES (?, ?, ?)
        """,
        (
            decision_history_id,
            json_text(history.get("manager_input_json")),
            json_text(history.get("manager_output_json")),
        ),
    )
    conn.execute("UPDATE manager_decision_history SET candidate_id=?, pipeline_version_json=? WHERE id=?",
                 (history.get("candidate_id"), json_text(history.get("pipeline_version")), decision_history_id))
    stamp_candidate_catalyst(conn, 'manager_decision_history', decision_history_id, history.get('candidate_id'))
    record_decision_gate_verdicts(decision_history_id, history.get("manager_output_json"), conn=conn)
    snapshot = {
        "version": SNAPSHOT_VERSION,
        "company_id": company_id,
        "symbol": symbol,
        "company_name": company_name,
        "decision_run_at": decision_run_at,
        "strategist_decision": clean_text(history.get("strategist_decision")),
        "manager_decision": clean_text(history.get("manager_decision")),
        "manager_confidence": clean_text(history.get("manager_confidence")),
        "manager_reason": clean_text(history.get("manager_reason")),
        "target_dte_bucket": clean_text(history.get("target_dte_bucket")),
        "selected_option_id": clean_text(history.get("selected_option_id")),
        "selected_option_symbol": clean_text(history.get("selected_option_symbol")),
        "selected_expiration_date": clean_text(history.get("selected_expiration_date")),
        "selected_strike_price": safe_float(history.get("selected_strike_price")),
        "selected_option_source": clean_text(history.get("selected_option_source")),
        "outcome_status": clean_text(history.get("outcome_status")),
        "realized_pnl_pct": safe_float(history.get("realized_pnl_pct")),
        "closed_at": clean_text(history.get("closed_at")),
    }
    conn.execute(
        """
        INSERT INTO decision_autopsy_ledger (
            manager_decision_history_id, symbol, decision_run_at, snapshot_json, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (decision_history_id, symbol, decision_run_at, json_text(snapshot), decision_run_at),
    )

    ensure_decision_evidence_snapshot(
        {
            "id": decision_history_id,
            "decision_run_at": decision_run_at,
            "manager_input_json": history.get("manager_input_json"),
            "manager_output_json": history.get("manager_output_json"),
        },
        conn=conn,
    )
    return decision_history_id


def update_manager_decision_latest_pnl(
    *,
    decision_history_id: int,
    latest_trade_pnl_pct: float | None,
    latest_trade_pnl_updated_at: Any = None,
    pnl_expires_at: Any = None,
    resolved_outcome_label: str | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> bool:
    normalized_id = coerce_int(decision_history_id)
    if normalized_id is None:
        return False

    if conn is None:
        initialize_database(db_path=db_path)
        with closing(get_connection(db_path)) as local_conn, local_conn:
            return update_manager_decision_latest_pnl(
                decision_history_id=decision_history_id,
                latest_trade_pnl_pct=latest_trade_pnl_pct,
                latest_trade_pnl_updated_at=latest_trade_pnl_updated_at,
                pnl_expires_at=pnl_expires_at,
                resolved_outcome_label=resolved_outcome_label,
                conn=local_conn,
            )

    updated_at = normalize_timestamp(latest_trade_pnl_updated_at)
    cursor = conn.execute(
        """
        UPDATE manager_decision_history
        SET
            latest_trade_pnl_pct = ?,
            latest_trade_pnl_updated_at = ?,
            pnl_expires_at = ?,
            resolved_outcome_label = ?,
            outcome_status = CASE
                WHEN outcome_status = 'closed' THEN outcome_status
                WHEN ? LIKE 'managed_exit_submitted_%' THEN 'exit_pending'
                ELSE 'open'
            END,
            updated_at = ?
        WHERE id = ? AND COALESCE(outcome_status, '') != 'closed'
        """,
        (
            safe_float(latest_trade_pnl_pct),
            updated_at,
            normalize_timestamp(pnl_expires_at) if pnl_expires_at not in (None, "") else None,
            clean_text(resolved_outcome_label),
            clean_text(resolved_outcome_label),
            updated_at,
            normalized_id,
        ),
    )
    return int(cursor.rowcount or 0) > 0


def record_manager_decision_closed_outcome(
    *,
    decision_history_id: int,
    realized_pnl_pct: float,
    closed_at: Any,
    resolved_outcome_label: str | None = None,
    broker_confirmed: bool = False,
    closing_order_ids: list[str] | None = None,
    exit_reason: str | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Persist a closed outcome only after the broker confirms the final exit."""
    normalized_id = coerce_int(decision_history_id)
    normalized_pnl = safe_float(realized_pnl_pct)
    if normalized_id is None or normalized_pnl is None or not broker_confirmed:
        return False
    try:
        datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if conn is None:
        initialize_database(db_path=db_path)
        with closing(get_connection(db_path)) as local_conn, local_conn:
            return record_manager_decision_closed_outcome(
                decision_history_id=normalized_id,
                realized_pnl_pct=normalized_pnl,
                closed_at=closed_at,
                resolved_outcome_label=resolved_outcome_label,
                broker_confirmed=True,
                closing_order_ids=closing_order_ids,
                exit_reason=exit_reason,
                conn=local_conn,
            )

    normalized_closed_at = normalize_timestamp(closed_at)
    if not exit_reason and closing_order_ids:
        exit_row = conn.execute(
            "SELECT decision FROM option_trade_executions WHERE order_id = ?",
            (closing_order_ids[-1],),
        ).fetchone()
        exit_reason = exit_row["decision"] if exit_row else "broker_close"

    cursor = conn.execute(
        """
        UPDATE manager_decision_history
        SET
            outcome_status = 'closed',
            exit_reason = ?,
            closing_order_ids_json = ?,
            realized_pnl_pct = ?,
            closed_at = ?,
            resolved_outcome_label = COALESCE(?, resolved_outcome_label),
            updated_at = ?
        WHERE id = ?
          AND trade_executed = 1
          AND COALESCE(outcome_status, '') != 'closed'
        """,
        (
            clean_text(exit_reason),
            json_text(closing_order_ids or []),
            normalized_pnl,
            normalized_closed_at,
            clean_text(resolved_outcome_label),
            normalized_closed_at,
            normalized_id,
        ),
    )
    if int(cursor.rowcount or 0) > 0:
        close_position_outcome(normalized_id, realized_pnl_pct=normalized_pnl,
                               closed_at=normalized_closed_at, exit_reason=exit_reason, conn=conn,
                               closing_order_id=closing_order_ids[-1] if closing_order_ids else None)
        return True
    return False


def expire_manager_decision_pnl(
    *,
    now: Any = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    expiration_cutoff = normalize_timestamp(now)
    if conn is None:
        initialize_database(db_path=db_path)
        with closing(get_connection(db_path)) as local_conn, local_conn:
            return expire_manager_decision_pnl(now=now, conn=local_conn)

    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM manager_decision_history
        WHERE pnl_expires_at IS NOT NULL
          AND pnl_expires_at <= ?
          AND (
              latest_trade_pnl_pct IS NOT NULL
              OR latest_trade_pnl_updated_at IS NOT NULL
              OR resolved_outcome_label IS NOT NULL
          )
        """,
        (expiration_cutoff,),
    ).fetchone()
    return int((row["count"] if row is not None else 0) or 0)


def list_unresolved_manager_decisions(*, db_path=None) -> list[dict]:
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        return [dict(row) for row in conn.execute("""
            SELECT id, selected_option_symbol, trade_execution_order_id, decision_run_at
            FROM manager_decision_history
            WHERE trade_executed = 1
              AND COALESCE(outcome_status, '') != 'closed'
              AND COALESCE(trade_execution_order_id, '') != ''
              AND COALESCE(selected_option_symbol, '') != ''
            ORDER BY decision_run_at, id
        """).fetchall()]
