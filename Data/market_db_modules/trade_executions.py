from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from db_common import DB_PATH, clean_text, coerce_float, get_connection, json_text
from .bootstrap import initialize_database
from .shared import coerce_int, normalize_timestamp


def record_option_trade_execution(
    execution: dict[str, Any],
    *,
    paper: bool,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> int | None:
    if not isinstance(execution, dict):
        return None

    if not execution.get("submitted"):
        return None

    order = execution.get("order")
    if not isinstance(order, dict):
        return None

    order_id = clean_text(order.get("id"))
    option_symbol = clean_text(
        execution.get("selected_option_symbol")
        or order.get("symbol")
    )
    if order_id is None or option_symbol is None:
        return None

    if conn is None:
        initialize_database(db_path=db_path)
        with get_connection(db_path) as local_conn:
            return record_option_trade_execution(
                execution,
                paper=paper,
                conn=local_conn,
            )

    submitted_at = normalize_timestamp(
        order.get("submitted_at")
        or execution.get("submitted_at")
        or execution.get("ran_at")
    )
    values = (
        coerce_int(execution.get("company_id")),
        order_id,
        clean_text(execution.get("symbol")),
        clean_text(execution.get("name")),
        option_symbol,
        clean_text(execution.get("decision")),
        clean_text(execution.get("confidence")),
        clean_text(execution.get("selected_option_id")),
        clean_text(execution.get("selected_option_source")),
        clean_text(execution.get("selected_expiration_date")),
        coerce_float(execution.get("selected_strike_price")),
        coerce_int(execution.get("order_qty") or order.get("qty")),
        coerce_float(execution.get("estimated_order_cost")),
        coerce_float(execution.get("available_buying_power")),
        coerce_float(execution.get("max_deployable_buying_power")),
        coerce_float(execution.get("remaining_deployable_buying_power")),
        1 if paper else 0,
        clean_text(order.get("status")),
        clean_text(order.get("side")),
        clean_text(order.get("type")),
        clean_text(order.get("time_in_force")),
        submitted_at,
        json_text(execution),
    )
    cursor = conn.execute(
        """
        INSERT INTO option_trade_executions (
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
            raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_id) DO UPDATE SET
            company_id = excluded.company_id,
            underlying_symbol = excluded.underlying_symbol,
            company_name = excluded.company_name,
            option_symbol = excluded.option_symbol,
            decision = excluded.decision,
            confidence = excluded.confidence,
            selected_option_id = excluded.selected_option_id,
            selected_option_source = excluded.selected_option_source,
            expiration_date = excluded.expiration_date,
            strike_price = excluded.strike_price,
            order_qty = excluded.order_qty,
            estimated_order_cost = excluded.estimated_order_cost,
            available_buying_power = excluded.available_buying_power,
            max_deployable_buying_power = excluded.max_deployable_buying_power,
            remaining_deployable_buying_power = excluded.remaining_deployable_buying_power,
            paper = excluded.paper,
            order_status = excluded.order_status,
            order_side = excluded.order_side,
            order_type = excluded.order_type,
            time_in_force = excluded.time_in_force,
            submitted_at = excluded.submitted_at,
            raw_json = excluded.raw_json
        RETURNING id
        """,
        values,
    )
    return int(cursor.fetchone()["id"])
