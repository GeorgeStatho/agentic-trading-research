from __future__ import annotations

from contextlib import closing
from functools import cache
import sqlite3
from pathlib import Path

from db_common import (
    DATA_DIR,
    applied_setup_fingerprint,
    database_path,
    get_connection,
    lock_for_migration,
    mark_setup_current,
    record_setup_fingerprint,
    setup_fingerprint,
    setup_is_current,
    table_columns,
    table_exists,
)
from .autopsy import backfill_decision_autopsy_ledger, move_payload_copies_out_of_ledger
from .contract_research import CLUSTER_PATH
from .entities import add_company, add_industry, add_sector
from .shared import LEGACY_SECTOR_KEY, LEGACY_SECTOR_NAME, SCHEMA_PATH, is_legacy_schema
from services.core.numbers import safe_float


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


def _ensure_thesis_validation_attempts(conn: sqlite3.Connection) -> None:
    if table_exists(conn, "thesis_validation_runs") and "attempts" not in table_columns(conn, "thesis_validation_runs"):
        conn.execute("ALTER TABLE thesis_validation_runs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1")


def _ensure_account_position_exposure(conn: sqlite3.Connection) -> None:
    columns = table_columns(conn, "account_positions")
    for column in ("entry_spot", "entry_delta", "source_delta"):
        if column not in columns:
            conn.execute(f"ALTER TABLE account_positions ADD COLUMN {column} REAL")


def _ensure_llm_usage_stage(conn: sqlite3.Connection) -> None:
    if "stage" not in table_columns(conn, "llm_call_usage"):
        conn.execute("ALTER TABLE llm_call_usage ADD COLUMN stage TEXT")


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
            market_weight=safe_float(company.get("market_cap")),
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

    Write paths call this before every write. The work runs only when this process has not
    already confirmed the file current and the database was last set up by different setup
    files; otherwise it is one lookup, or none.
    """
    db_path = database_path(db_path)
    fingerprint = _setup_fingerprint(Path(schema_path).resolve())
    if not setup_is_current(SETUP_NAME, db_path, fingerprint):
        _set_up(db_path, schema_path, fingerprint)
        mark_setup_current(SETUP_NAME, db_path, fingerprint)  # only after success


def _schema_file(name: str):
    def run(conn: sqlite3.Connection) -> None:
        conn.executescript(Path(__file__).with_name(name).read_text(encoding="utf-8"))
    run.__name__ = f"run_{name}"
    return run


def _setup_steps() -> tuple:
    """The steps after the base schema, in order.

    Every step must be idempotent: any edit under Data/ changes the setup fingerprint and re-runs
    them all, on new and existing databases alike. Append a schema change as a new step. The
    imports are here because those modules import this one.
    """
    from catalyst_schema import migrate_catalysts
    from .compact_storage import migrate_compact_storage
    from .contract_research import migrate_contract_research
    from .pricing_research import backfill_raw_residuals
    from .research_foundation import migrate_research_foundation
    from .telemetry_schema import migrate_telemetry_columns

    return (
        _ensure_manager_decision_outcome_columns,
        _ensure_thesis_validation_attempts,
        _move_manager_decision_payloads,
        migrate_telemetry_columns,
        migrate_catalysts,
        _schema_file("pricing_schema.sql"),
        backfill_raw_residuals,
        migrate_contract_research,
        _schema_file("research_schema.sql"),
        migrate_research_foundation,
        _schema_file("company_collection_schema.sql"),
        _schema_file("company_interest_schema.sql"),
        _schema_file("discovery_schema.sql"),
        _schema_file("research_runtime_schema.sql"),
        _schema_file("evidence_thesis_schema.sql"),
        _schema_file("trade_economics_schema.sql"),
        _schema_file("thesis_event_reviews_schema.sql"),
        _schema_file("forward_evaluation_schema.sql"),
        _schema_file("account_positions_schema.sql"),
        _ensure_account_position_exposure,
        migrate_compact_storage,
        move_payload_copies_out_of_ledger,
        lambda conn: backfill_decision_autopsy_ledger(conn=conn),
        _ensure_llm_usage_stage,
    )


def _set_up(db_path: Path | str, schema_path: Path | str, fingerprint: str) -> None:
    # sqlite3 keeps a self-referencing statement cache, so a connection left to the garbage
    # collector holds the file open long after the caller is done with it.
    with closing(get_connection(db_path)) as conn, conn:
        if applied_setup_fingerprint(conn, SETUP_NAME) == fingerprint:
            return
        if is_legacy_schema(conn):
            _migrate_legacy_schema(conn)
        else:
            conn.executescript(Path(schema_path).read_text(encoding="utf-8"))
        for step in _setup_steps():
            step(conn)
        record_setup_fingerprint(conn, SETUP_NAME, fingerprint)
