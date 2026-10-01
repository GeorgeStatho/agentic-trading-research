"""Immutable evidence-thesis checks and shadow thesis-call allowances. No model calls here."""
from contextlib import closing
import json

from db_common import DB_PATH, get_connection
from research_foundation_contracts import timestamp
from .foundation_store import canonical, digest, insert_record, load_record
from .research_runtime import ResearchBudgetExceeded, daily_reserved_cost


TERMINAL_RESEARCH_STATUSES = ("ready", "deferred_missing_evidence", "conflicting_evidence",
                              "budget_exhausted", "superseded", "failed")


def evidence_thesis_id(record):
    if record.get("research_session_id"):
        return "thesis:research:" + digest(record["research_session_id"])
    return "thesis:legacy:" + digest(record)


def record_evidence_thesis(record, *, db_path=DB_PATH):
    data = json.loads(canonical({**record, "created_at": timestamp(record["created_at"])}))
    identity = evidence_thesis_id(data)
    data["evaluation_id"] = identity
    with closing(get_connection(db_path)) as conn, conn:
        insert_record(conn, "evidence_thesis_checks", "evaluation_id", data, {
            "route": data["route"], "company_id": data["company_id"], "status": data["status"],
            "package_id": data.get("package_id"), "candidate_id": data.get("candidate_id"),
            "research_session_id": data.get("research_session_id"), "created_at": data["created_at"]})
        if data.get("research_session_id"):
            conn.execute("DELETE FROM research_thesis_progress WHERE session_id=?", (data["research_session_id"],))
    return identity


def get_evidence_thesis(evaluation_id, *, db_path=DB_PATH):
    return load_record(db_path, "evidence_thesis_checks", "evaluation_id", evaluation_id)


def latest_strategist_thesis_check(candidate_id, *, db_path=DB_PATH):
    """The legacy-route thesis check written for a candidate, if Phase 4 ran for it."""
    if not candidate_id:
        return None
    with closing(get_connection(db_path)) as conn:
        row = conn.execute("""SELECT evaluation_id FROM evidence_thesis_checks
            WHERE candidate_id=? AND route='legacy_strategist' ORDER BY created_at DESC LIMIT 1""",
                           (candidate_id,)).fetchone()
    return get_evidence_thesis(row["evaluation_id"], db_path=db_path) if row else None


def pending_research_theses(*, limit, db_path=DB_PATH):
    """Terminal research sessions without a thesis check, oldest first."""
    placeholders = ",".join("?" for _ in TERMINAL_RESEARCH_STATUSES)
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute(f"""SELECT s.session_id FROM research_sessions s
            LEFT JOIN evidence_thesis_checks t ON t.research_session_id=s.session_id
            WHERE t.evaluation_id IS NULL AND s.status IN ({placeholders})
            ORDER BY s.rowid LIMIT ?""", (*TERMINAL_RESEARCH_STATUSES, limit)).fetchall()
    return [row["session_id"] for row in rows]


def research_thesis_reservation(session_id, *, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn:
        row = conn.execute("SELECT * FROM research_thesis_progress WHERE session_id=?", (session_id,)).fetchone()
    return {**dict(row), "reservation": json.loads(row["reservation_json"])} if row else None


def reserve_research_thesis(session_id, reservation, *, at, daily_calls, daily_cost_budget, db_path=DB_PATH):
    """Reserve one thesis call before dispatch, sharing the research daily cost budget."""
    day = timestamp(at)[:10]
    cost = reservation["cost_reserved_usd"]
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT calls FROM research_thesis_allowances WHERE day=?", (day,)).fetchone()
        if row and row["calls"] >= daily_calls:
            raise ResearchBudgetExceeded("daily_thesis_allowance")
        if daily_reserved_cost(conn, day) + cost > daily_cost_budget:
            raise ResearchBudgetExceeded("daily_cost_allowance")
        conn.execute("""INSERT INTO research_thesis_allowances VALUES (?,1,?) ON CONFLICT(day) DO UPDATE
            SET calls=calls+1,reserved_cost=reserved_cost+excluded.reserved_cost""", (day, cost))
        conn.execute("INSERT INTO research_thesis_progress VALUES (?,?,?)",
                     (session_id, timestamp(at), canonical({**reservation, "reserved_day": day})))
    return day


def charge_research_thesis_usage(day, extra_cost, *, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute("UPDATE research_thesis_allowances SET reserved_cost=reserved_cost+? WHERE day=?",
                     (extra_cost, day))
