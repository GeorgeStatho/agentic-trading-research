from __future__ import annotations

from contextlib import closing
from functools import cache
import sqlite3
from pathlib import Path

from db_common import (DATA_DIR, applied_setup_fingerprint, coerce_float, get_connection,
                       lock_for_migration, record_setup_fingerprint, setup_fingerprint, table_columns,
                       table_exists)
from .autopsy import backfill_decision_autopsy_ledger, move_payload_copies_out_of_ledger
from .contract_research import CLUSTER_PATH
from .entities import add_company, add_industry, add_sector
from .shared import LEGACY_SECTOR_KEY, LEGACY_SECTOR_NAME, SCHEMA_PATH, is_legacy_schema


SETUP_NAME = "market"


_MANAGER_DECISION_OUTCOME_COLUMNS = {
    "exit_reason": "TEXT",
    "closing_order_ids_json": "TEXT",
    "outcome_status": "TEXT",
    "realized_pnl_pct": "REAL",
    "closed_at": "TEXT",
}


def _ensure_manager_decision_outcome_columns(conn: sqlite3.Connection) -> None:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'manager_decision_history'"
    ).fetchone()
    if table_exists is None:
        return
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(manager_decision_history)").fetchall()
    }
    for column_name, column_type in _MANAGER_DECISION_OUTCOME_COLUMNS.items():
        if column_name not in columns:
            conn.execute(f"ALTER TABLE manager_decision_history ADD COLUMN {column_name} {column_type}")


def _move_manager_decision_payloads(conn: sqlite3.Connection) -> None:
    """Move the manager JSON that older code kept on manager_decision_history into its own table."""
    lock_for_migration(conn)
    if "manager_input_json" not in table_columns(conn, "manager_decision_history"):
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO manager_decision_payloads (
            manager_decision_history_id, manager_input_json, manager_output_json
        )
        SELECT id, manager_input_json, manager_output_json
        FROM manager_decision_history
        WHERE manager_input_json IS NOT NULL OR manager_output_json IS NOT NULL
        """
    )
    conn.execute("ALTER TABLE manager_decision_history DROP COLUMN manager_input_json")
    conn.execute("ALTER TABLE manager_decision_history DROP COLUMN manager_output_json")


def _migrate_legacy_schema(conn: sqlite3.Connection) -> None:
    legacy_industries = []
    legacy_companies = []

    if conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'industries'"
    ).fetchone():
        legacy_industries = [dict(row) for row in conn.execute("SELECT * FROM industries").fetchall()]
    if conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'companies'"
    ).fetchone():
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


@cache
def _setup_fingerprint(schema_path: Path) -> str:
    module_dir = Path(__file__).parent
    return setup_fingerprint([*DATA_DIR.glob("*.py"), *DATA_DIR.glob("*.sql"), *module_dir.glob("*.py"),
                              *module_dir.glob("*.sql"), CLUSTER_PATH, schema_path])


def initialize_database(
    db_path: Path | str | None = None,
    schema_path: Path | str = SCHEMA_PATH,
) -> None:
    """Create and migrate the market schema.

    Write paths call this before every write, so the work runs only when the database was
    last set up by different setup files; otherwise it is one lookup.
    """
    fingerprint = _setup_fingerprint(Path(schema_path).resolve())
    # sqlite3 keeps a self-referencing statement cache, so a connection left to the garbage
    # collector holds the file open long after the caller is done with it.
    with closing(get_connection(db_path)) as conn, conn:
        if applied_setup_fingerprint(conn, SETUP_NAME) == fingerprint:
            return
        if is_legacy_schema(conn):
            _migrate_legacy_schema(conn)
        else:
            conn.executescript(Path(schema_path).read_text(encoding="utf-8"))
        _ensure_manager_decision_outcome_columns(conn)
        _move_manager_decision_payloads(conn)
        from .telemetry_schema import migrate_telemetry_columns
        migrate_telemetry_columns(conn)
        from catalyst_schema import migrate_catalysts
        migrate_catalysts(conn)
        conn.executescript(Path(__file__).with_name('pricing_schema.sql').read_text(encoding='utf-8'))
        from .pricing_research import backfill_raw_residuals
        backfill_raw_residuals(conn)
        from .contract_research import migrate_contract_research
        migrate_contract_research(conn)
        conn.executescript(Path(__file__).with_name('research_schema.sql').read_text(encoding='utf-8'))
        from .research_foundation import migrate_research_foundation
        migrate_research_foundation(conn)
        conn.executescript(Path(__file__).with_name('company_collection_schema.sql').read_text(encoding='utf-8'))
        conn.executescript(Path(__file__).with_name('company_interest_schema.sql').read_text(encoding='utf-8'))
        conn.executescript(Path(__file__).with_name('discovery_schema.sql').read_text(encoding='utf-8'))
        conn.executescript(Path(__file__).with_name('research_runtime_schema.sql').read_text(encoding='utf-8'))
        conn.executescript(Path(__file__).with_name('evidence_thesis_schema.sql').read_text(encoding='utf-8'))
        conn.executescript(Path(__file__).with_name('trade_economics_schema.sql').read_text(encoding='utf-8'))
        conn.executescript(Path(__file__).with_name('thesis_event_reviews_schema.sql').read_text(encoding='utf-8'))
        conn.executescript(Path(__file__).with_name('forward_evaluation_schema.sql').read_text(encoding='utf-8'))
        move_payload_copies_out_of_ledger(conn)
        backfill_decision_autopsy_ledger(conn=conn)
        record_setup_fingerprint(conn, SETUP_NAME, fingerprint)
