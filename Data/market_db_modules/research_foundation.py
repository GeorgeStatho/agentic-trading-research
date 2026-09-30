"""Foundation storage only; queue workers and investigative tools land in later phases."""
from contextlib import closing
from pathlib import Path

from db_common import DB_PATH, get_connection
from research_foundation_contracts import DiscoveryQueueItem, ResearchSession, TradeEconomicsReport
from .foundation_store import digest, insert_record, load_record, read_record, record_data
from .bootstrap import initialize_database


def migrate_research_foundation(conn):
    conn.executescript(Path(__file__).with_name("research_foundation_schema.sql").read_text())
    # These records are append-only. Queue/session progress gets its own mutable state.
    for table in ("evidence_documents", "company_facts", "evidence_packages",
                  "research_observations", "candidate_observations", "research_run_versions",
                  "strategy_evaluations", "trade_economics_reports", "research_steps"):
        for operation in ("UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation.lower()}
                BEFORE {operation} ON {table} BEGIN
                SELECT RAISE(ABORT, 'Immutable research record'); END""")


def enqueue_discovery(item: DiscoveryQueueItem, *, db_path=DB_PATH):
    initialize_database(db_path=db_path)
    data = record_data(item)
    identity_fields = ("company_id", "catalyst_id", "evidence_revision", "purpose")
    queue_id = "queue:" + digest({key: data[key] for key in identity_fields})
    data["queue_id"] = queue_id
    with closing(get_connection(db_path)) as conn, conn:
        # The same event arriving through another discovery route remains one item.
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT queue_id FROM discovery_queue WHERE queue_id=?", (queue_id,)).fetchone()
        if existing:
            return queue_id
        insert_record(conn, "discovery_queue", "queue_id", data, {
            **{key: data[key] for key in identity_fields}, "next_attempt_at": data["created_at"],
        })
    return queue_id


def get_discovery_item(queue_id, *, db_path=DB_PATH):
    return load_record(db_path, "discovery_queue", "queue_id", queue_id)


def record_research_session(session: ResearchSession, *, db_path=DB_PATH):
    initialize_database(db_path=db_path)
    data = record_data(session)
    with closing(get_connection(db_path)) as conn, conn:
        item = read_record(conn, "discovery_queue", "queue_id", session.queue_id)
        for package_id in (session.initial_package_id, session.final_package_id):
            if package_id is None:
                continue
            package = read_record(conn, "evidence_packages", "package_id", package_id)
            if package["company_id"] != item["company_id"] or package["catalyst_id"] != item["catalyst_id"]:
                raise ValueError("Session evidence does not match its queued company event")
            if package["event_revision"] != item["evidence_revision"]:
                raise ValueError("Session evidence revision differs from its queued event")
        return insert_record(conn, "research_sessions", "session_id", data, {
            "queue_id": session.queue_id, "initial_package_id": session.initial_package_id,
            "final_package_id": session.final_package_id, "status": session.status,
        })


def get_research_session(session_id, *, db_path=DB_PATH):
    return load_record(db_path, "research_sessions", "session_id", session_id)


def record_trade_economics_report(report: TradeEconomicsReport, *, db_path=DB_PATH):
    initialize_database(db_path=db_path)
    data = record_data(report)
    with closing(get_connection(db_path)) as conn, conn:
        package = read_record(conn, "evidence_packages", "package_id", report.package_id)
        candidate = conn.execute("SELECT symbol FROM candidates WHERE candidate_id=?", (report.candidate_id,)).fetchone()
        company = conn.execute("SELECT symbol FROM companies WHERE id=?", (package["company_id"],)).fetchone()
        if candidate is None or company is None or candidate["symbol"] != company["symbol"]:
            raise ValueError("Economics report candidate and package do not match")
        if package["information_cutoff_time"] > data["created_at"]:
            raise ValueError("Economics report includes future evidence")
        if report.evaluation_id:
            evaluation = conn.execute("SELECT candidate_id FROM strategy_evaluations WHERE evaluation_id=?",
                                      (report.evaluation_id,)).fetchone()
            if evaluation is None or evaluation["candidate_id"] != report.candidate_id:
                raise ValueError("Economics evaluation does not match its candidate")
        return insert_record(conn, "trade_economics_reports", "report_id", data, {
            "candidate_id": report.candidate_id, "package_id": report.package_id,
            "evaluation_id": report.evaluation_id, "created_at": data["created_at"],
        })


def get_trade_economics_report(report_id, *, db_path=DB_PATH):
    return load_record(db_path, "trade_economics_reports", "report_id", report_id)
