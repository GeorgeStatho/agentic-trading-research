"""The V2 gate verdicts recorded against one decision's candidate.

The rollout view counts verdicts across a window; a decision record needs the
opposite cut — which V2 gates judged this candidate, and what they would have
done had they been enforcing. A `shadow=1` rejection is a verdict the gate
recorded without acting on it, so a decision carrying one still traded.

Nothing here initializes or migrates the database. A missing lifecycle table,
or an older `manager_decision_history` with no candidate link, is reported as
unreadable rather than as "no gate objected" — the two mean opposite things to
anyone reading a decision.
"""
from __future__ import annotations

from contextlib import closing
from typing import Any, Iterable

from api_support.context import DB_PATH, get_connection
from api_support.v2_status import GUARDED_FEATURES, SHADOW_ONLY_FEATURES

# Reuses the rollout view's names so a stage cannot be called two things.
STAGE_LABELS: dict[str, str] = {
    **{stage: feature["label"] for feature in GUARDED_FEATURES for stage in feature["stages"]},
    **{feature["stage"]: feature["label"] for feature in SHADOW_ONLY_FEATURES},
}


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _column_exists(conn, table: str, column: str) -> bool:
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def shadow_verdicts_for_decisions(
    decision_ids: Iterable[Any], *, db_path=DB_PATH
) -> dict[int, list[dict]] | None:
    """Verdicts per `manager_decision_history.id`, or None when unreadable.

    Decisions with no verdict are simply absent from the mapping. The caller
    bounds the id count; the autopsy route caps its page well inside SQLite's
    limit on bound variables.
    """
    ids = sorted({int(value) for value in decision_ids if value})
    with closing(get_connection(db_path)) as conn:
        if not _table_exists(conn, "candidate_rejections"):
            return None
        if not _column_exists(conn, "manager_decision_history", "candidate_id"):
            return None
        if not ids:
            return {}
        verdicts: dict[int, list[dict]] = {}
        placeholders = ",".join("?" * len(ids))
        for row in conn.execute(
            f"""SELECT history.id AS decision_id, rejection.stage, rejection.reason_code,
                       rejection.shadow, rejection.occurred_at
                FROM manager_decision_history history
                JOIN candidate_rejections rejection
                    ON rejection.candidate_id = history.candidate_id
                WHERE history.id IN ({placeholders})
                ORDER BY history.id, rejection.stage, rejection.reason_code""",
            ids,
        ):
            verdicts.setdefault(int(row["decision_id"]), []).append(
                {
                    "stage": row["stage"],
                    "label": STAGE_LABELS.get(row["stage"], row["stage"]),
                    "reason_code": row["reason_code"],
                    "shadow": bool(row["shadow"]),
                    "occurred_at": row["occurred_at"],
                }
            )
        return verdicts
