from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from db_common import DB_PATH, DATA_DIR, clean_text, coerce_float, get_connection, initialize_schema, json_text, table_columns, table_exists


SCHEMA_PATH = DATA_DIR / "market_schema.sql"
SECTOR_DEFINITIONS_PATH = DATA_DIR / "sector_etfs.json"
LEGACY_SECTOR_KEY = "unassigned"
LEGACY_SECTOR_NAME = "Unassigned"


def _normalize_timestamp(value: Any) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()

    text = clean_text(value)
    if text is None:
        return datetime.now(timezone.utc).isoformat()

    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return datetime.now(timezone.utc).isoformat()

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _extract_snapshot_price(snapshot: dict[str, Any]) -> float | None:
    for key in ("price", "last_price", "lastPrice", "regularMarketPrice", "ask_price", "bid_price"):
        value = coerce_float(snapshot.get(key))
        if value is not None:
            return value
    return None


def _extract_snapshot_volume(snapshot: dict[str, Any]) -> float | None:
    for key in ("volume", "day_volume", "dayVolume", "regularMarketVolume"):
        value = coerce_float(snapshot.get(key))
        if value is not None:
            return value
    return None


def _coerce_int(value: Any) -> int | None:
    text = clean_text(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _normalize_bool_to_int(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    text = clean_text(value)
    if text is None:
        return 0
    return 1 if text.lower() in {"true", "yes", "1"} else 0


def _find_company_row_by_symbol(
    symbol: str | None,
    *,
    conn: sqlite3.Connection,
) -> sqlite3.Row | None:
    normalized_symbol = clean_text(symbol)
    if normalized_symbol is None:
        return None
    return conn.execute(
        """
        SELECT id, symbol, name
        FROM companies
        WHERE symbol = ?
        LIMIT 1
        """,
        (normalized_symbol,),
    ).fetchone()


def _is_legacy_schema(conn: sqlite3.Connection) -> bool:
    if not table_exists(conn, "industries"):
        return False
    return "sector_id" not in table_columns(conn, "industries")


def add_sector(
    sector_key: str,
    name: str,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> int:
    values = (sector_key, name, json_text(raw_json))
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_sector(sector_key, name, raw_json=raw_json, conn=local_conn)

    cursor = conn.execute(
        """
        INSERT INTO sectors (sector_key, name, raw_json)
        VALUES (?, ?, ?)
        ON CONFLICT(sector_key) DO UPDATE SET
            name = excluded.name,
            raw_json = excluded.raw_json
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def add_industry(
    sector_id: int,
    industry_key: str,
    name: str,
    symbol: Optional[str] = None,
    market_weight: Optional[float] = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> int:
    values = (sector_id, industry_key, name, symbol, market_weight, json_text(raw_json))
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_industry(
                sector_id,
                industry_key,
                name,
                symbol=symbol,
                market_weight=market_weight,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO industries (sector_id, industry_key, name, symbol, market_weight, raw_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(industry_key) DO UPDATE SET
            sector_id = excluded.sector_id,
            name = excluded.name,
            symbol = excluded.symbol,
            market_weight = excluded.market_weight,
            raw_json = excluded.raw_json
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def add_company(
    industry_id: int,
    symbol: str,
    name: str,
    rating: Optional[str] = None,
    market_weight: Optional[float] = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> int:
    values = (industry_id, symbol, name, rating, market_weight, json_text(raw_json))
    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_company(
                industry_id,
                symbol,
                name,
                rating=rating,
                market_weight=market_weight,
                raw_json=raw_json,
                conn=local_conn,
            )

    cursor = conn.execute(
        """
        INSERT INTO companies (industry_id, symbol, name, rating, market_weight, raw_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            industry_id = excluded.industry_id,
            name = excluded.name,
            rating = excluded.rating,
            market_weight = excluded.market_weight,
            raw_json = excluded.raw_json
        RETURNING id
        """,
        values,
    )
    return cursor.fetchone()["id"]


def replace_industry_company_rankings(
    industry_id: int,
    ranking_type: str,
    ranked_companies: list[tuple[int, int, Any | None]],
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> None:
    if conn is None:
        with get_connection(db_path) as local_conn:
            replace_industry_company_rankings(
                industry_id,
                ranking_type,
                ranked_companies,
                conn=local_conn,
            )
            return

    conn.execute(
        """
        DELETE FROM industry_company_rankings
        WHERE industry_id = ? AND ranking_type = ?
        """,
        (industry_id, ranking_type),
    )

    for company_id, rank, raw_json in ranked_companies:
        conn.execute(
            """
            INSERT INTO industry_company_rankings (
                industry_id,
                company_id,
                ranking_type,
                rank,
                raw_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (industry_id, company_id, ranking_type, rank, json_text(raw_json)),
            )


def get_market_data_refresh_state(
    resource_type: str,
    resource_key: str,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> sqlite3.Row | None:
    normalized_type = clean_text(resource_type)
    normalized_key = clean_text(resource_key)
    if normalized_type is None or normalized_key is None:
        return None

    if conn is None:
        with get_connection(db_path) as local_conn:
            return get_market_data_refresh_state(
                normalized_type,
                normalized_key,
                conn=local_conn,
            )

    return conn.execute(
        """
        SELECT resource_type, resource_key, last_hydrated_at, raw_json, created_at
        FROM market_data_refresh_state
        WHERE resource_type = ? AND resource_key = ?
        LIMIT 1
        """,
        (normalized_type, normalized_key),
    ).fetchone()


def record_market_data_refresh_state(
    resource_type: str,
    resource_key: str,
    *,
    last_hydrated_at: Any | None = None,
    raw_json: Any | None = None,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> None:
    normalized_type = clean_text(resource_type)
    normalized_key = clean_text(resource_key)
    if normalized_type is None or normalized_key is None:
        return

    values = (
        normalized_type,
        normalized_key,
        _normalize_timestamp(last_hydrated_at),
        json_text(raw_json),
    )

    if conn is None:
        with get_connection(db_path) as local_conn:
            record_market_data_refresh_state(
                normalized_type,
                normalized_key,
                last_hydrated_at=last_hydrated_at,
                raw_json=raw_json,
                conn=local_conn,
            )
            return

    conn.execute(
        """
        INSERT INTO market_data_refresh_state (resource_type, resource_key, last_hydrated_at, raw_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(resource_type, resource_key) DO UPDATE SET
            last_hydrated_at = excluded.last_hydrated_at,
            raw_json = excluded.raw_json
        """,
        values,
    )


def add_company_price_snapshot(
    symbol: str,
    snapshot: dict[str, Any],
    *,
    source: str = "yfinance_stream",
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> int | None:
    normalized_symbol = clean_text(symbol)
    if normalized_symbol is None:
        return None

    if conn is None:
        with get_connection(db_path) as local_conn:
            return add_company_price_snapshot(
                normalized_symbol,
                snapshot,
                source=source,
                conn=local_conn,
            )

    company_row = conn.execute(
        """
        SELECT id
        FROM companies
        WHERE symbol = ?
        """,
        (normalized_symbol,),
    ).fetchone()
    if company_row is None:
        return None

    company_id = int(company_row["id"])
    captured_at = _normalize_timestamp(
        snapshot.get("captured_at")
        or snapshot.get("timestamp")
        or snapshot.get("time")
        or snapshot.get("market_time")
    )
    price = _extract_snapshot_price(snapshot)
    volume = _extract_snapshot_volume(snapshot)

    previous_row = conn.execute(
        """
        SELECT price
        FROM company_price_snapshots
        WHERE company_id = ?
        ORDER BY captured_at DESC, id DESC
        LIMIT 1
        """,
        (company_id,),
    ).fetchone()
    previous_price = coerce_float(previous_row["price"]) if previous_row is not None else None

    price_change = None
    price_change_pct = None
    if price is not None and previous_price is not None:
        price_change = price - previous_price
        if previous_price != 0:
            price_change_pct = price_change / previous_price

    cursor = conn.execute(
        """
        INSERT INTO company_price_snapshots (
            company_id,
            symbol,
            captured_at,
            price,
            previous_price,
            price_change,
            price_change_pct,
            volume,
            source,
            raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            company_id,
            normalized_symbol,
            captured_at,
            price,
            previous_price,
            price_change,
            price_change_pct,
            volume,
            source,
            json_text(snapshot),
        ),
    )
    return int(cursor.fetchone()["id"])


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

    submitted_at = _normalize_timestamp(
        order.get("submitted_at")
        or execution.get("submitted_at")
        or execution.get("ran_at")
    )
    values = (
        _coerce_int(execution.get("company_id")),
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
        _coerce_int(execution.get("order_qty") or order.get("qty")),
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

    company_id = _coerce_int(history.get("company_id"))
    symbol = clean_text(history.get("symbol"))
    company_name = clean_text(history.get("company_name"))
    if company_id is None and symbol is not None:
        company_row = _find_company_row_by_symbol(symbol, conn=conn)
        if company_row is not None:
            company_id = _coerce_int(company_row["id"])
            symbol = symbol or clean_text(company_row["symbol"])
            company_name = company_name or clean_text(company_row["name"])

    decision_run_at = _normalize_timestamp(
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
        _normalize_bool_to_int(history.get("trade_executed")),
        clean_text(history.get("trade_execution_order_id")),
        _coerce_int(history.get("trade_execution_record_id")),
        coerce_float(history.get("latest_trade_pnl_pct")),
        (
            _normalize_timestamp(history.get("latest_trade_pnl_updated_at"))
            if history.get("latest_trade_pnl_updated_at") not in (None, "")
            else None
        ),
        (
            _normalize_timestamp(history.get("pnl_expires_at"))
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
    return int(cursor.lastrowid)


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
        with get_connection(db_path) as local_conn:
            return get_latest_open_manager_decision_for_trade_link(
                company_id=company_id,
                symbol=symbol,
                selected_option_symbol=selected_option_symbol,
                submitted_at=submitted_at,
                conn=local_conn,
            )

    normalized_company_id = _coerce_int(company_id)
    normalized_symbol = clean_text(symbol)
    submitted_at_text = _normalize_timestamp(submitted_at)
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
        with get_connection(db_path) as local_conn:
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

    updated_at = _normalize_timestamp(submitted_at)
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
            _coerce_int(trade_execution_record_id),
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
        with get_connection(db_path) as local_conn:
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
    normalized_id = _coerce_int(decision_history_id)
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

    updated_at = _normalize_timestamp(latest_trade_pnl_updated_at)
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
            _normalize_timestamp(pnl_expires_at) if pnl_expires_at not in (None, "") else None,
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
    expiration_cutoff = _normalize_timestamp(now)
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


def _migrate_legacy_schema(conn: sqlite3.Connection) -> None:
    legacy_industries = []
    legacy_companies = []

    if table_exists(conn, "industries"):
        legacy_industries = [dict(row) for row in conn.execute("SELECT * FROM industries").fetchall()]
    if table_exists(conn, "companies"):
        legacy_companies = [dict(row) for row in conn.execute("SELECT * FROM companies").fetchall()]

    conn.execute("DROP TABLE IF EXISTS companies")
    conn.execute("DROP TABLE IF EXISTS industries")
    conn.execute("DROP TABLE IF EXISTS sectors")
    conn.executescript(Path(SCHEMA_PATH).read_text(encoding="utf-8"))

    if not legacy_industries:
        return

    sector_id = add_sector(LEGACY_SECTOR_KEY, LEGACY_SECTOR_NAME, conn=conn)
    industry_id_map: dict[int, int] = {}

    for industry in legacy_industries:
        new_id = add_industry(
            sector_id,
            industry_key=str(industry["name"]).strip().lower().replace(" ", "-"),
            name=industry["name"],
            raw_json=industry,
            conn=conn,
        )
        industry_id_map[industry["id"]] = new_id

    for company in legacy_companies:
        legacy_industry_id = company.get("industry_id")
        if legacy_industry_id not in industry_id_map:
            continue

        raw_json = {
            "headquarters": company.get("headquarters"),
            "market_cap": company.get("market_cap"),
        }
        add_company(
            industry_id_map[legacy_industry_id],
            symbol=company.get("ticker") or company["name"],
            name=company["name"],
            market_weight=coerce_float(company.get("market_cap")),
            raw_json=raw_json,
            conn=conn,
        )


def initialize_database(db_path: Path | str = DB_PATH, schema_path: Path | str = SCHEMA_PATH) -> None:
    with get_connection(db_path) as conn:
        if _is_legacy_schema(conn):
            _migrate_legacy_schema(conn)
        else:
            conn.executescript(Path(schema_path).read_text(encoding="utf-8"))


def load_sector_definitions(
    sector_definitions: dict[str, dict[str, Any]],
    db_path: Path | str = DB_PATH,
) -> None:
    initialize_database(db_path=db_path)

    with get_connection(db_path) as conn:
        for sector_key, payload in sector_definitions.items():
            if not isinstance(payload, dict):
                payload = {}

            normalized_sector_key = clean_text(sector_key)
            if normalized_sector_key is None:
                continue

            sector_name = (
                clean_text(payload.get("sector_name"))
                or clean_text(payload.get("name"))
                or normalized_sector_key.replace("-", " ").title()
            )
            add_sector(
                normalized_sector_key,
                sector_name,
                raw_json=payload,
                conn=conn,
            )


def load_sector_definitions_from_json(
    json_path: Path | str = SECTOR_DEFINITIONS_PATH,
    db_path: Path | str = DB_PATH,
) -> None:
    payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Top-level sector definitions payload must be a dictionary.")
    load_sector_definitions(payload, db_path=db_path)


def load_sector_tree(
    sector_tree: dict[str, dict[str, Any]],
    db_path: Path | str = DB_PATH,
) -> None:
    initialize_database(db_path=db_path)

    with get_connection(db_path) as conn:
        for sector_key, sector_payload in sector_tree.items():
            industries_payload = sector_payload
            sector_name = sector_key.replace("-", " ").title()
            sector_raw = None

            if isinstance(sector_payload, dict) and "industries" in sector_payload:
                industries_payload = sector_payload.get("industries", {})
                sector_name = sector_payload.get("name", sector_name)
                sector_raw = sector_payload

            sector_id = add_sector(sector_key, sector_name, raw_json=sector_raw, conn=conn)

            if not isinstance(industries_payload, dict):
                continue

            for industry_key, industry_payload in industries_payload.items():
                if not isinstance(industry_payload, dict):
                    continue

                industry_id = add_industry(
                    sector_id,
                    industry_key=industry_key,
                    name=industry_payload.get("name", industry_key.replace("-", " ").title()),
                    symbol=industry_payload.get("symbol"),
                    market_weight=coerce_float(industry_payload.get("market weight") or industry_payload.get("market_weight")),
                    raw_json=industry_payload,
                    conn=conn,
                )

                companies_payload = industry_payload.get("companies", {})
                if not isinstance(companies_payload, dict):
                    continue

                company_ids_by_symbol: dict[str, int] = {}
                for company_symbol, company_payload in companies_payload.items():
                    if not isinstance(company_payload, dict):
                        continue

                    normalized_symbol = clean_text(company_payload.get("symbol")) or clean_text(company_symbol)
                    normalized_name = clean_text(company_payload.get("name")) or normalized_symbol
                    if normalized_symbol is None or normalized_name is None:
                        continue

                    company_id = add_company(
                        industry_id,
                        symbol=normalized_symbol,
                        name=normalized_name,
                        rating=clean_text(company_payload.get("rating")),
                        market_weight=coerce_float(company_payload.get("market weight") or company_payload.get("market_weight")),
                        raw_json=company_payload,
                        conn=conn,
                    )
                    company_ids_by_symbol[normalized_symbol] = company_id

                for ranking_type, payload_key in (
                    ("top_growth", "top_growth_companies"),
                    ("top_performing", "top_performing_companies"),
                ):
                    ranking_payload = industry_payload.get(payload_key, {})
                    ranked_companies: list[tuple[int, int, Any | None]] = []
                    if isinstance(ranking_payload, dict):
                        for rank, (company_symbol, company_payload) in enumerate(ranking_payload.items(), start=1):
                            if not isinstance(company_payload, dict):
                                continue

                            normalized_symbol = clean_text(company_payload.get("symbol")) or clean_text(company_symbol)
                            normalized_name = clean_text(company_payload.get("name")) or normalized_symbol
                            if normalized_symbol is None or normalized_name is None:
                                continue

                            company_id = company_ids_by_symbol.get(normalized_symbol)
                            if company_id is None:
                                company_id = add_company(
                                    industry_id,
                                    symbol=normalized_symbol,
                                    name=normalized_name,
                                    rating=clean_text(company_payload.get("rating")),
                                    market_weight=coerce_float(
                                        company_payload.get("market weight") or company_payload.get("market_weight")
                                    ),
                                    raw_json=company_payload,
                                    conn=conn,
                                )
                                company_ids_by_symbol[normalized_symbol] = company_id

                            ranked_companies.append((company_id, rank, company_payload))

                    replace_industry_company_rankings(
                        industry_id,
                        ranking_type,
                        ranked_companies,
                        conn=conn,
                    )


def load_sector_tree_from_json(
    json_path: Path | str,
    db_path: Path | str = DB_PATH,
) -> None:
    payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Top-level JSON payload must be a dictionary.")
    load_sector_tree(payload, db_path=db_path)


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
    normalized_company_id = _coerce_int(company_id)
    now_text = _normalize_timestamp(None)
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
            _normalize_timestamp(
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
    if normalized_symbol is not None:
        query += " WHERE underlying_symbol = ?"
        parameters.append(normalized_symbol)

    query += " ORDER BY submitted_at DESC, id DESC LIMIT ?"
    parameters.append(max(1, int(limit)))

    with get_connection(db_path) as conn:
        rows = conn.execute(query, tuple(parameters)).fetchall()
    return rows


if __name__ == "__main__":
    initialize_database()
    json_path = DATA_DIR / "sectors_companies.json"
    if json_path.exists() and json_path.read_text(encoding="utf-8").strip():
        load_sector_tree_from_json(json_path)
        print(f"Loaded sector tree from {json_path} into {DB_PATH}")
    else:
        print(f"Initialized market database at {DB_PATH}")
