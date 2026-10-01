"""Read-only shadow-period summary of evidence thesis checks and EVIDENCE_THESIS gate verdicts.

Outcome figures are descriptive counts for monitoring. Promotion evidence comes from the
Part 6 research reports, which pair gate verdicts with counterfactual outcomes.
"""
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json

from db_common import DB_PATH, get_connection
from .evidence_thesis import TERMINAL_RESEARCH_STATUSES
from .foundation_store import digest


TOP_SHAPE_ERRORS = 5
DIRECTIONAL_DECISIONS = {"call", "put"}


def _share(count, total):
    return round(count / total, 4) if total else None


def _counts(counter):
    return dict(counter.most_common())


def _load_checks(conn, since, route):
    # Phase 7 challenger checks share the strategist route but are not production's strategist.
    query, values = ("SELECT record_json,record_hash FROM evidence_thesis_checks "
                     "WHERE (candidate_id IS NULL OR candidate_id NOT LIKE 'paired:%')"), []
    if since:
        query += " AND julianday(created_at)>=julianday(?)"
        values.append(since)
    if route:
        query += " AND route=?"
        values.append(route)
    checks = []
    for row in conn.execute(query + " ORDER BY created_at", values):
        record = json.loads(row["record_json"])
        if digest(record) != row["record_hash"]:
            raise ValueError("Corrupt evidence_thesis_checks record hash")
        checks.append(record)
    return checks


def _check_summary(checks):
    statuses = Counter(check["status"] for check in checks)
    requested = [check for check in checks if check["status"] != "not_requested"]
    claims = [claim for check in checks for claim in check.get("claims", [])]
    return {
        "total": len(checks),
        "by_route": _counts(Counter(check["route"] for check in checks)),
        "by_status": {status: {"count": count, "share": _share(count, len(checks))}
                      for status, count in statuses.most_common()},
        "requested": len(requested),
        "invalid_output_rate": _share(statuses["invalid_output"], len(requested)),
        "deferred_rate": _share(statuses["deferred"], len(requested)),
        "not_requested_reasons": _counts(Counter(
            reason for check in checks if check["status"] == "not_requested" for reason in check["failures"])),
        "thesis_failures": _counts(Counter(code for check in requested for code in check["failures"])),
        # Claim codes such as unsupported_number:$150 million are grouped by their prefix.
        "claim_failures": _counts(Counter(code.split(":")[0] for claim in claims for code in claim["failures"])),
        "claims": {"total": len(claims), "verified": sum(c["status"] == "verified" for c in claims),
                   "central": sum(c["central"] for c in claims),
                   "central_failed": sum(c["central"] and c["status"] == "failed" for c in claims),
                   "references_only": sum(c["content_check"] == "references_only" for c in claims)},
        "shape_errors": Counter(check.get("shape_error") for check in checks
                                if check["status"] == "invalid_output").most_common(TOP_SHAPE_ERRORS),
        "directions": _counts(Counter(check["thesis"]["direction"] for check in checks if check.get("thesis"))),
        "semantic_review": _counts(Counter(check["semantic_review"]["status"] for check in checks)),
    }


def _mean_outcome(rows):
    values = [row["realized_pnl_pct"] for row in rows if row["realized_pnl_pct"] is not None]
    return {"closed_trades": len(values), "mean_realized_pnl_pct": sum(values) / len(values) if values else None}


def _gate_summary(conn, since):
    # decision_run_at is written by the runner; the window is approximate for naive timestamps.
    query = "SELECT trade_executed,realized_pnl_pct,manager_output_json FROM manager_decision_history"
    rows = conn.execute(query + (" WHERE julianday(decision_run_at)>=julianday(?)" if since else ""),
                        [since] if since else []).fetchall()
    verdicts = []
    for row in rows:
        output = json.loads(row["manager_output_json"] or "{}")
        gate = output.get("evidence_thesis_gate")
        if isinstance(gate, dict):
            verdicts.append((row, gate, output))
    directional = [(row, gate, output) for row, gate, output in verdicts
                   if gate.get("proposed_decision") in DIRECTIONAL_DECISIONS]
    would_block = [(row, gate, output) for row, gate, output in directional if gate.get("would_allow") is False]
    other_gates_pass = [item for item in would_block
                        if (item[2].get("entry_gate") or {}).get("allowed") is True
                        and (item[2].get("market_regime_gate") or {}).get("allowed") is True
                        and item[2].get("selected_option")]
    executed = [row for row, _, _ in directional if row["trade_executed"]]
    executed_blocked = [row for row, gate, _ in directional if row["trade_executed"] and gate.get("would_allow") is False]
    executed_allowed = [row for row, gate, _ in directional if row["trade_executed"] and gate.get("would_allow") is True]
    return {
        "evaluated": len(verdicts),
        "enforcement_modes": _counts(Counter(gate.get("enforcement_mode") for _, gate, _ in verdicts)),
        "directional": len(directional),
        "would_block": len(would_block),
        "would_block_rate": _share(len(would_block), len(directional)),
        "blocked": sum(gate.get("allowed") is False for _, gate, _ in directional),
        "would_block_reasons": _counts(Counter(code for _, gate, _ in would_block for code in gate.get("reason_codes", []))),
        # Entries that passed every other gate and had a contract: what enforcement would remove.
        "would_block_otherwise_eligible": len(other_gates_pass),
        "executed_trades": len(executed),
        "executed_while_would_block": len(executed_blocked),
        "descriptive_outcomes": {"would_block": _mean_outcome(executed_blocked),
                                 "would_allow": _mean_outcome(executed_allowed)},
    }


def _research_route_summary(conn):
    placeholders = ",".join("?" for _ in TERMINAL_RESEARCH_STATUSES)
    pending = conn.execute(f"""SELECT COUNT(*) FROM research_sessions s
        LEFT JOIN evidence_thesis_checks t ON t.research_session_id=s.session_id
        WHERE t.evaluation_id IS NULL AND s.status IN ({placeholders})""", TERMINAL_RESEARCH_STATUSES).fetchone()[0]
    reserved = conn.execute("SELECT COUNT(*) FROM research_thesis_progress").fetchone()[0]
    return {"sessions_awaiting_thesis": pending, "unresolved_reservations": reserved}


def evidence_thesis_report(*, days=7, route=None, now=None, db_path=DB_PATH):
    """Summarise the last ``days`` days (all history when ``days`` is 0)."""
    if type(days) is not int or days < 0:
        raise ValueError("days must be a non-negative integer")
    at = now or datetime.now(timezone.utc)
    since = (at - timedelta(days=days)).isoformat() if days else None
    with closing(get_connection(db_path)) as conn:
        return {"window": {"since": since, "until": at.isoformat(), "route": route},
                "checks": _check_summary(_load_checks(conn, since, route)),
                "entry_gate": _gate_summary(conn, since),
                "research_route": _research_route_summary(conn),
                "note": "Descriptive monitoring only; use Part 6 research reports for promotion evidence."}
