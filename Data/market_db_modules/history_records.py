from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from db_common import DB_PATH, clean_text, coerce_float, get_connection, json_text
from .bootstrap import initialize_database
from .shared import coerce_int, find_company_row_by_symbol, normalize_bool_to_int, normalize_timestamp


def record_manager_decision_history(
    history: dict[str, Any],
    *,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> int | None:
    if not isinstance(history, dict):
        return None

    if conn is None:
        initialize_database(db_path=db_path)
        with get_connection(db_path) as local_conn:
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
        coerce_float(history.get("selected_strike_price")),
        clean_text(history.get("selected_option_source")),
        json_text(history.get("manager_input_json")),
        json_text(history.get("manager_output_json")),
        normalize_bool_to_int(history.get("trade_executed")),
        clean_text(history.get("trade_execution_order_id")),
        coerce_int(history.get("trade_execution_record_id")),
        coerce_float(history.get("latest_trade_pnl_pct")),
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
            manager_input_json,
            manager_output_json,
            trade_executed,
            trade_execution_order_id,
            trade_execution_record_id,
            latest_trade_pnl_pct,
            latest_trade_pnl_updated_at,
            pnl_expires_at,
            resolved_outcome_label,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )
    decision_history_id = int(cursor.lastrowid)
    snapshot = {
        "version": "decision-autopsy-v1",
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
        "selected_strike_price": coerce_float(history.get("selected_strike_price")),
        "selected_option_source": clean_text(history.get("selected_option_source")),
        "manager_input": history.get("manager_input_json") if isinstance(history.get("manager_input_json"), dict) else {},
        "manager_output": history.get("manager_output_json") if isinstance(history.get("manager_output_json"), dict) else {},
    }
    conn.execute(
        """
        INSERT INTO decision_autopsy_ledger (
            manager_decision_history_id, symbol, decision_run_at, snapshot_json, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (decision_history_id, symbol, decision_run_at, json_text(snapshot), decision_run_at),
    )
    return decision_history_id


def update_manager_decision_latest_pnl(
    *,
    decision_history_id: int,
    latest_trade_pnl_pct: float | None,
    latest_trade_pnl_updated_at: Any = None,
    pnl_expires_at: Any = None,
    resolved_outcome_label: str | None = None,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> bool:
    normalized_id = coerce_int(decision_history_id)
    if normalized_id is None:
        return False

    if conn is None:
        initialize_database(db_path=db_path)
        with get_connection(db_path) as local_conn:
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
            updated_at = ?
        WHERE id = ?
        """,
        (
            coerce_float(latest_trade_pnl_pct),
            updated_at,
            normalize_timestamp(pnl_expires_at) if pnl_expires_at not in (None, "") else None,
            clean_text(resolved_outcome_label),
            updated_at,
            normalized_id,
        ),
    )
    return int(cursor.rowcount or 0) > 0


def expire_manager_decision_pnl(
    *,
    now: Any = None,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> int:
    expiration_cutoff = normalize_timestamp(now)
    if conn is None:
        initialize_database(db_path=db_path)
        with get_connection(db_path) as local_conn:
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
