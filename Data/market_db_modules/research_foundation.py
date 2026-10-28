"""Foundation storage only; queue workers and investigative tools land in later phases."""
from contextlib import closing
from pathlib import Path

from db_common import get_connection, lock_for_migration
from research_foundation_contracts import DiscoveryQueueItem, ResearchSession
from .foundation_store import digest, insert_record, load_record, read_record, record_data
from .bootstrap import initialize_database


def migrate_research_foundation(conn):
    conn.executescript(Path(__file__).with_name("research_foundation_schema.sql").read_text())
    # These records are append-only. Queue/session progress gets its own mutable state.
    for table in ("evidence_documents", "company_facts", "evidence_packages",
                  "research_observations", "candidate_observations", "research_run_versions",
                  "strategy_evaluations", "research_steps"):
        for operation in ("UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation.lower()}
                BEFORE {operation} ON {table} BEGIN
                SELECT RAISE(ABORT, 'Immutable research record'); END""")
    drop_unused_economics_table(conn)
    backfill_evidence_document_index(conn)


def backfill_evidence_document_index(conn):
    """Index the documents recorded before evidence_document_fts existed; later rows follow by trigger."""
    lock_for_migration(conn)
    conn.execute("""INSERT INTO evidence_document_fts (document_id, company_id, text)
        SELECT document_id, json_extract(record_json, '$.metadata.company_id'), json_extract(record_json, '$.text')
        FROM evidence_documents WHERE document_id NOT IN (SELECT document_id FROM evidence_document_fts)""")


def drop_unused_economics_table(conn):
    """Phase 0's package-bound economics table was never written; candidate_economics_reports replaced it.

    Dropped only while empty, so no recorded data can be lost.
    """
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_economics_reports'").fetchone():
        return
    if conn.execute("SELECT COUNT(*) FROM trade_economics_reports").fetchone()[0] == 0:
        conn.execute("DROP TABLE trade_economics_reports")


def enqueue_discovery(item: DiscoveryQueueItem, *, db_path=None):
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


def get_discovery_item(queue_id, *, db_path=None):
    return load_record(db_path, "discovery_queue", "queue_id", queue_id)


def record_research_session(session: ResearchSession, *, db_path=None):
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


def get_research_session(session_id, *, db_path=None):
    return load_record(db_path, "research_sessions", "session_id", session_id)
