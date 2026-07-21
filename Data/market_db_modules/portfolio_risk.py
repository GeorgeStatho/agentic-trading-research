from __future__ import annotations

from datetime import datetime
from pathlib import Path

from db_common import DB_PATH, clean_text, coerce_float, get_connection
from .bootstrap import initialize_database
from .shared import coerce_int, normalize_timestamp


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

