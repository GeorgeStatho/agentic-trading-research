from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path
from typing import Any

from db_common import DB_PATH, clean_text, get_connection
from .bootstrap import initialize_database
from .shared import coerce_int, normalize_timestamp


def get_latest_open_manager_decision_for_trade_link(
    *,
    company_id: int | None = None,
    symbol: str | None = None,
    selected_option_symbol: str | None = None,
    submitted_at: Any = None,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> sqlite3.Row | None:
    normalized_option_symbol = clean_text(selected_option_symbol)
    if normalized_option_symbol is None:
        return None

    if conn is None:
        initialize_database(db_path=db_path)
        with closing(get_connection(db_path)) as local_conn, local_conn:
            return get_latest_open_manager_decision_for_trade_link(
                company_id=company_id,
                symbol=symbol,
                selected_option_symbol=selected_option_symbol,
                submitted_at=submitted_at,
                conn=local_conn,
            )

    normalized_company_id = coerce_int(company_id)
    normalized_symbol = clean_text(symbol)
    submitted_at_text = normalize_timestamp(submitted_at)
    query = """
        SELECT
            id,
            company_id,
            symbol,
            selected_option_symbol,
            decision_run_at,
            trade_executed
        FROM manager_decision_history
        WHERE selected_option_symbol = ?
          AND decision_run_at <= ?
          AND COALESCE(trade_executed, 0) = 0
    """
    parameters: list[Any] = [normalized_option_symbol, submitted_at_text]
    if normalized_company_id is not None:
        query += " AND company_id = ?"
        parameters.append(normalized_company_id)
    elif normalized_symbol is not None:
        query += " AND symbol = ?"
        parameters.append(normalized_symbol)
    query += " ORDER BY decision_run_at DESC, id DESC LIMIT 2"

    rows = conn.execute(query, tuple(parameters)).fetchall()
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    if str(rows[0]["decision_run_at"] or "") == str(rows[1]["decision_run_at"] or ""):
        return None
    return rows[0]


def link_manager_decision_to_trade_execution(
    *,
    trade_execution_order_id: str,
    trade_execution_record_id: int | None = None,
    company_id: int | None = None,
    symbol: str | None = None,
    selected_option_symbol: str | None = None,
    submitted_at: Any = None,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> int | None:
    normalized_order_id = clean_text(trade_execution_order_id)
    if normalized_order_id is None:
        return None

    if conn is None:
        initialize_database(db_path=db_path)
        with closing(get_connection(db_path)) as local_conn, local_conn:
            return link_manager_decision_to_trade_execution(
                trade_execution_order_id=trade_execution_order_id,
                trade_execution_record_id=trade_execution_record_id,
                company_id=company_id,
                symbol=symbol,
                selected_option_symbol=selected_option_symbol,
                submitted_at=submitted_at,
                conn=local_conn,
            )

    matched_row = get_latest_open_manager_decision_for_trade_link(
        company_id=company_id,
        symbol=symbol,
        selected_option_symbol=selected_option_symbol,
        submitted_at=submitted_at,
        conn=conn,
    )
    if matched_row is None:
        return None

    updated_at = normalize_timestamp(submitted_at)
    conn.execute(
        """
        UPDATE manager_decision_history
        SET
            trade_executed = 1,
            trade_execution_order_id = ?,
            trade_execution_record_id = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            normalized_order_id,
            coerce_int(trade_execution_record_id),
            updated_at,
            int(matched_row["id"]),
        ),
    )
    return int(matched_row["id"])


def get_latest_linked_manager_decision_for_option_symbol(
    *,
    selected_option_symbol: str,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> sqlite3.Row | None:
    normalized_option_symbol = clean_text(selected_option_symbol)
    if normalized_option_symbol is None:
        return None

    if conn is None:
        initialize_database(db_path=db_path)
        with closing(get_connection(db_path)) as local_conn, local_conn:
            return get_latest_linked_manager_decision_for_option_symbol(
                selected_option_symbol=selected_option_symbol,
                conn=local_conn,
            )

    return conn.execute(
        """
        SELECT
            id,
            company_id,
            symbol,
            selected_option_symbol,
            decision_run_at,
            trade_executed,
            trade_execution_order_id,
            trade_execution_record_id,
            latest_trade_pnl_pct,
            pnl_expires_at
        FROM manager_decision_history
        WHERE selected_option_symbol = ?
          AND COALESCE(trade_executed, 0) = 1
        ORDER BY decision_run_at DESC, id DESC
        LIMIT 1
        """,
        (normalized_option_symbol,),
    ).fetchone()
