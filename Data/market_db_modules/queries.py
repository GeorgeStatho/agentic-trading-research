from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from db_common import DB_PATH, clean_text, get_connection
from .shared import coerce_int, normalize_timestamp


def list_companies_by_industry(industry_key: str, db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.symbol, c.name, c.rating, c.market_weight
            FROM companies AS c
            JOIN industries AS i ON i.id = c.industry_id
            WHERE i.industry_key = ?
            ORDER BY c.market_weight DESC, c.symbol
            """,
            (industry_key,),
        ).fetchall()
    return rows


def list_industry_company_rankings(
    industry_key: str,
    ranking_type: str,
    db_path: Path | str = DB_PATH,
) -> list[sqlite3.Row]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                r.rank,
                r.ranking_type,
                c.id AS company_id,
                c.symbol,
                c.name,
                c.rating,
                c.market_weight
            FROM industry_company_rankings AS r
            JOIN industries AS i ON i.id = r.industry_id
            JOIN companies AS c ON c.id = r.company_id
            WHERE i.industry_key = ? AND r.ranking_type = ?
            ORDER BY r.rank ASC, c.symbol ASC
            """,
            (industry_key, ranking_type),
        ).fetchall()
    return rows


def list_company_price_snapshots(
    symbol: str,
    *,
    limit: int = 20,
    db_path: Path | str = DB_PATH,
) -> list[sqlite3.Row]:
    normalized_symbol = clean_text(symbol)
    if normalized_symbol is None:
        return []

    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                s.id,
                s.company_id,
                s.symbol,
                s.captured_at,
                s.price,
                s.previous_price,
                s.price_change,
                s.price_change_pct,
                s.volume,
                s.source,
                s.raw_json
            FROM company_price_snapshots AS s
            WHERE s.symbol = ?
            ORDER BY s.captured_at DESC, s.id DESC
            LIMIT ?
            """,
            (normalized_symbol, max(1, int(limit))),
        ).fetchall()
    return rows


def list_recent_manager_decision_history(
    *,
    underlying_symbol: str | None = None,
    company_id: int | None = None,
    limit: int = 20,
    within_days: int | None = None,
    include_expired_pnl: bool = False,
    db_path: Path | str = DB_PATH,
) -> list[sqlite3.Row]:
    normalized_symbol = clean_text(underlying_symbol)
    normalized_company_id = coerce_int(company_id)
    now_text = normalize_timestamp(None)
    query = """
        SELECT
            id,
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
            CASE
                WHEN ? = 1 THEN latest_trade_pnl_pct
                WHEN pnl_expires_at IS NULL OR pnl_expires_at > ? THEN latest_trade_pnl_pct
                ELSE NULL
            END AS latest_trade_pnl_pct,
            CASE
                WHEN ? = 1 THEN latest_trade_pnl_updated_at
                WHEN pnl_expires_at IS NULL OR pnl_expires_at > ? THEN latest_trade_pnl_updated_at
                ELSE NULL
            END AS latest_trade_pnl_updated_at,
            pnl_expires_at,
            CASE
                WHEN ? = 1 THEN resolved_outcome_label
                WHEN pnl_expires_at IS NULL OR pnl_expires_at > ? THEN resolved_outcome_label
                ELSE NULL
            END AS resolved_outcome_label,
            created_at,
            updated_at
        FROM manager_decision_history
        WHERE 1 = 1
    """
    parameters: list[Any] = [
        1 if include_expired_pnl else 0,
        now_text,
        1 if include_expired_pnl else 0,
        now_text,
        1 if include_expired_pnl else 0,
        now_text,
    ]
    if normalized_company_id is not None:
        query += " AND company_id = ?"
        parameters.append(normalized_company_id)
    elif normalized_symbol is not None:
        query += " AND symbol = ?"
        parameters.append(normalized_symbol)

    if within_days is not None and int(within_days) > 0:
        query += " AND decision_run_at >= ?"
        parameters.append(
            normalize_timestamp(
                datetime.now(timezone.utc) - timedelta(days=max(1, int(within_days)))
            )
        )

    query += " ORDER BY decision_run_at DESC, id DESC LIMIT ?"
    parameters.append(max(1, int(limit)))

    with get_connection(db_path) as conn:
        rows = conn.execute(query, tuple(parameters)).fetchall()
    return rows


def list_option_trade_executions(
    *,
    underlying_symbol: str | None = None,
    within_days: int | None = None,
    limit: int = 100,
    db_path: Path | str = DB_PATH,
) -> list[sqlite3.Row]:
    normalized_symbol = clean_text(underlying_symbol)
    query = """
        SELECT
            id,
            company_id,
            order_id,
            underlying_symbol,
            company_name,
            option_symbol,
            decision,
            confidence,
            selected_option_id,
            selected_option_source,
            expiration_date,
            strike_price,
            order_qty,
            estimated_order_cost,
            available_buying_power,
            max_deployable_buying_power,
            remaining_deployable_buying_power,
            paper,
            order_status,
            order_side,
            order_type,
            time_in_force,
            submitted_at,
            recorded_at,
            raw_json
        FROM option_trade_executions
    """
    parameters: list[Any] = []
    where_clauses: list[str] = []
    if normalized_symbol is not None:
        where_clauses.append("underlying_symbol = ?")
        parameters.append(normalized_symbol)

    if within_days is not None and int(within_days) > 0:
        where_clauses.append("submitted_at >= ?")
        parameters.append(
            normalize_timestamp(
                datetime.now(timezone.utc) - timedelta(days=max(1, int(within_days)))
            )
        )

    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)

    query += " ORDER BY submitted_at DESC, id DESC LIMIT ?"
    parameters.append(max(1, int(limit)))

    with get_connection(db_path) as conn:
        rows = conn.execute(query, tuple(parameters)).fetchall()
    return rows
