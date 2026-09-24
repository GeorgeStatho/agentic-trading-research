from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from db_common import DB_PATH, clean_text, coerce_float, get_connection
from .bootstrap import initialize_database
from .shared import coerce_int, normalize_timestamp


def _recorded_at_boundary(value: datetime | str) -> str | None:
    """Normalize a boundary to the UTC ``YYYY-MM-DD HH:MM:SS`` format that
    ``option_trade_executions.recorded_at`` uses (SQLite ``CURRENT_TIMESTAMP``)."""
    if isinstance(value, datetime):
        parsed = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    normalized = normalize_timestamp(value)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_company_sector_key(
    *,
    symbol: str | None = None,
    company_id: int | None = None,
    db_path: Path | str = DB_PATH,
) -> str:
    """Return the persisted sector for a company, falling back to ``unknown``."""
    normalized_symbol = clean_text(symbol)
    normalized_company_id = coerce_int(company_id)
    if normalized_symbol is None and normalized_company_id is None:
        return "unknown"
    initialize_database(db_path=db_path)
    where_clause = "company.id = ?" if normalized_company_id is not None else "company.symbol = ?"
    parameter = normalized_company_id if normalized_company_id is not None else normalized_symbol
    with get_connection(db_path) as conn:
        row = conn.execute(
            f"""
            SELECT sector.sector_key
            FROM companies AS company
            LEFT JOIN industries AS industry ON industry.id = company.industry_id
            LEFT JOIN sectors AS sector ON sector.id = industry.sector_id
            WHERE {where_clause}
            LIMIT 1
            """,
            (parameter,),
        ).fetchone()
    return str(row["sector_key"] or "unknown").strip().lower() if row is not None else "unknown"


def get_realized_option_loss_since(
    *,
    start_at: datetime | str,
    db_path: Path | str = DB_PATH,
) -> float:
    """Sum persisted, broker-confirmed option losses after a time boundary."""
    normalized_start = normalize_timestamp(start_at)
    if not normalized_start:
        return 0.0
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT history.realized_pnl_pct, execution.estimated_order_cost
            FROM manager_decision_history AS history
            LEFT JOIN option_trade_executions AS execution
              ON execution.id = history.trade_execution_record_id
              OR execution.order_id = history.trade_execution_order_id
            WHERE LOWER(history.manager_decision) IN ('call', 'put')
              AND history.closed_at >= ?
              AND history.realized_pnl_pct < 0
            """,
            (normalized_start,),
        ).fetchall()
    total_loss = 0.0
    for row in rows:
        entry_notional = coerce_float(row["estimated_order_cost"])
        pnl_pct = coerce_float(row["realized_pnl_pct"])
        if entry_notional is None or entry_notional <= 0 or pnl_pct is None:
            continue
        total_loss += abs(entry_notional * pnl_pct / 100.0)
    return round(total_loss, 4)


def count_option_entry_executions_since(
    *,
    underlying_symbol: str,
    start_at: datetime | str,
    db_path: Path | str = DB_PATH,
) -> int:
    """Count journaled option BUY submissions for one underlying after a boundary.

    Used by the per-symbol entry attempt cap. Counts every submitted buy for the
    underlying (adding to an existing position is still an entry attempt).
    """
    normalized_symbol = clean_text(underlying_symbol)
    boundary = _recorded_at_boundary(start_at)
    if normalized_symbol is None or boundary is None:
        return 0
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS entry_count
            FROM option_trade_executions
            WHERE UPPER(COALESCE(underlying_symbol, '')) = ?
              AND LOWER(COALESCE(order_side, '')) LIKE '%buy%'
              AND REPLACE(SUBSTR(recorded_at, 1, 19), 'T', ' ') >= ?
            """,
            (normalized_symbol.upper(), boundary),
        ).fetchone()
    return int(row["entry_count"] or 0) if row is not None else 0


def get_latest_stop_loss_exit_at(
    *,
    underlying_symbol: str,
    db_path: Path | str = DB_PATH,
) -> str | None:
    """Return the most recent journaled stop-loss exit time for one underlying.

    Timestamps come back normalized to UTC ``YYYY-MM-DD HH:MM:SS`` so callers can
    compare them against a boundary in the same format. Used by the per-symbol
    stop-loss re-entry cooldown.
    """
    normalized_symbol = clean_text(underlying_symbol)
    if normalized_symbol is None:
        return None
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT MAX(REPLACE(SUBSTR(recorded_at, 1, 19), 'T', ' ')) AS last_stop_exit_at
            FROM option_trade_executions
            WHERE UPPER(COALESCE(underlying_symbol, '')) = ?
              AND LOWER(COALESCE(order_side, '')) LIKE '%sell%'
              AND LOWER(COALESCE(decision, '')) = 'stop_loss'
            """,
            (normalized_symbol.upper(),),
        ).fetchone()
    if row is None:
        return None
    last_stop_exit_at = clean_text(row["last_stop_exit_at"])
    return last_stop_exit_at

