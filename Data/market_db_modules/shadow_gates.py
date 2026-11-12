"""Persist contemporaneous gate verdicts and join them to resolved trade outcomes."""
from __future__ import annotations

from contextlib import closing
import json

from db_common import get_connection, json_text
from services.gates.entry_gates import ENTRY_GATES
from .bootstrap import initialize_database


def record_decision_gate_verdicts(decision_id, output, *, conn):
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except ValueError:
            return
    if not isinstance(output, dict):
        return
    # Each gate's row is named by its result key, so a shadow variant never shares the production gate's name.
    for key in (gate.result_key for gate in ENTRY_GATES if gate.records_verdict_row):
        gate = output.get(key)
        if not isinstance(gate, dict) or not isinstance(gate.get("would_allow"), bool):
            continue
        conn.execute("""
            INSERT INTO decision_gate_verdicts
                (manager_decision_history_id, gate_name, would_allow, mode, policy_version, verdict_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(manager_decision_history_id, gate_name) DO NOTHING
        """, (decision_id, key, int(gate["would_allow"]),
              gate.get("enforcement_mode") or gate.get("mode") or "unknown",
              gate.get("policy_version"), json_text(gate)))


def backfill_gate_verdicts(*, db_path=None):
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        before = conn.total_changes
        rows = conn.execute(
            "SELECT manager_decision_history_id, manager_output_json FROM manager_decision_payloads"
        ).fetchall()
        for row in rows:
            record_decision_gate_verdicts(row["manager_decision_history_id"], row["manager_output_json"], conn=conn)
        return conn.total_changes - before


def list_shadow_gate_outcomes(*, limit=100, db_path=None):
    """Last N executed decisions; unknown outcomes remain explicitly unscored.

    Summed percentage-point returns are diagnostic only, not dollar gate value
    or counterfactual evidence about candidates that never traded.
    """
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        return [dict(row) for row in conn.execute("""
            WITH recent AS (
                SELECT * FROM manager_decision_history WHERE trade_executed = 1
                ORDER BY decision_run_at DESC, id DESC LIMIT ?
            )
            SELECT v.gate_name, v.mode, v.policy_version, v.would_allow,
                COUNT(*) AS trade_count,
                SUM(CASE WHEN h.outcome_status = 'closed' AND h.realized_pnl_pct IS NOT NULL THEN 1 ELSE 0 END) AS resolved_count,
                SUM(CASE WHEN h.outcome_status = 'closed' AND h.realized_pnl_pct > 0 THEN 1 ELSE 0 END) AS winners,
                SUM(CASE WHEN h.outcome_status = 'closed' AND h.realized_pnl_pct < 0 THEN 1 ELSE 0 END) AS losers,
                SUM(CASE WHEN h.outcome_status = 'closed' AND h.realized_pnl_pct = 0 THEN 1 ELSE 0 END) AS flat,
                AVG(CASE WHEN h.outcome_status = 'closed' THEN h.realized_pnl_pct END) AS mean_realized_pnl_pct,
                SUM(CASE WHEN h.outcome_status = 'closed' THEN h.realized_pnl_pct END) AS sum_realized_pnl_pct
            FROM recent h JOIN decision_gate_verdicts v ON v.manager_decision_history_id = h.id
            GROUP BY v.gate_name, v.mode, v.policy_version, v.would_allow
            ORDER BY v.gate_name, v.mode, v.policy_version, v.would_allow
        """, (max(1, int(limit)),)).fetchall()]
