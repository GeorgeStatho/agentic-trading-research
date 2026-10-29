"""Stored benchmark sets (frozen decision ids with a dev/holdout split) and the runs scored on them.

Everything here is insert-only (see the triggers in market_schema.sql). A benchmark only screens
candidate challengers; nothing in this module touches `research_gate_promotions`.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Sequence

from db_common import get_connection, json_text
from .bootstrap import initialize_database


def insert_benchmark_set(set_id: str, *, selection_version: str, selection: dict[str, Any],
                         items: Sequence[dict[str, Any]], db_path: Path | str | None = None) -> None:
    """Store a set and its items together; a `set_id` already used raises `sqlite3.IntegrityError`.

    Each item is `{manager_decision_history_id, split, stratum}`.
    """
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute("INSERT INTO benchmark_sets (set_id, selection_version, selection_json, item_count, created_at) "
                     "VALUES (?, ?, ?, ?, ?)",
                     (set_id, selection_version, json_text(selection), len(items), datetime.now(timezone.utc).isoformat()))
        conn.executemany("INSERT INTO benchmark_items (set_id, manager_decision_history_id, split, stratum) VALUES (?, ?, ?, ?)",
                         [(set_id, item["manager_decision_history_id"], item["split"], item["stratum"]) for item in items])


def list_benchmark_items(set_id: str, *, split: str | None = None, db_path: Path | str | None = None) -> list[dict[str, Any]]:
    initialize_database(db_path=db_path)
    query = "SELECT manager_decision_history_id, split, stratum FROM benchmark_items WHERE set_id = ?"
    parameters: list[Any] = [set_id]
    if split is not None:
        query += " AND split = ?"
        parameters.append(split)
    with closing(get_connection(db_path)) as conn:
        return [dict(row) for row in conn.execute(query + " ORDER BY manager_decision_history_id", parameters)]


def benchmark_set_summary(set_id: str, *, db_path: Path | str | None = None) -> dict[str, Any]:
    """The set's selection record, item counts and symbols per split, and item counts per stratum and split."""
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn:
        saved = conn.execute("SELECT * FROM benchmark_sets WHERE set_id = ?", (set_id,)).fetchone()
        if saved is None:
            raise ValueError(f"No benchmark set '{set_id}'")
        rows = conn.execute("SELECT item.split, item.stratum, history.symbol FROM benchmark_items AS item "
                            "LEFT JOIN manager_decision_history AS history ON history.id = item.manager_decision_history_id "
                            "WHERE item.set_id = ?", (set_id,)).fetchall()
    splits = {split: {"items": sum(row["split"] == split for row in rows),
                      "symbols": len({row["symbol"] for row in rows if row["split"] == split})}
              for split in ("dev", "holdout")}
    strata = {stratum: {split: sum(row["stratum"] == stratum and row["split"] == split for row in rows)
                        for split in ("dev", "holdout")} for stratum in sorted({row["stratum"] for row in rows})}
    return {"set_id": set_id, "selection_version": saved["selection_version"], "selection": json.loads(saved["selection_json"]),
            "item_count": saved["item_count"], "created_at": saved["created_at"], "splits": splits, "strata": strata}


def primary_catalysts(*, db_path: Path | str | None = None) -> dict[str, tuple[str, dict[str, Any]]]:
    """Each candidate's primary catalyst as `(family, state)`."""
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn:
        return {row["candidate_id"]: (row["catalyst_family"], json.loads(row["state_json"]))
                for row in conn.execute("SELECT candidate_id, catalyst_family, state_json FROM candidate_catalysts "
                                        "WHERE is_primary = 1")}


def record_benchmark_run(*, set_id: str, split: str, model: str, prompt_versions: dict[str, Any], replay_strategist: bool,
                         scores: dict[str, Any], item_scores: dict[str, Any], backtest_run_id: int | None = None,
                         db_path: Path | str | None = None) -> int:
    """Store a run and return its id. A second holdout run of the same set, model, prompts and flag raises `sqlite3.IntegrityError`."""
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        return conn.execute(
            "INSERT INTO benchmark_runs (set_id, split, backtest_run_id, model, prompt_versions_json, replay_strategist, "
            "scores_json, item_scores_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (set_id, split, backtest_run_id, model, json_text(prompt_versions), int(replay_strategist), json_text(scores),
             json_text(item_scores), datetime.now(timezone.utc).isoformat())).lastrowid


def get_benchmark_run(run_id: int, *, db_path: Path | str | None = None) -> dict[str, Any] | None:
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn:
        row = conn.execute("SELECT * FROM benchmark_runs WHERE run_id = ?", (run_id,)).fetchone()
    return _run(row) if row else None


def list_benchmark_runs(set_id: str, *, split: str | None = None, model: str | None = None,
                        db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """The set's runs, oldest first."""
    initialize_database(db_path=db_path)
    query, parameters = "SELECT * FROM benchmark_runs WHERE set_id = ?", [set_id]
    for column, value in (("split", split), ("model", model)):
        if value is not None:
            query += f" AND {column} = ?"
            parameters.append(value)
    with closing(get_connection(db_path)) as conn:
        return [_run(row) for row in conn.execute(query + " ORDER BY run_id", parameters)]


def _run(row) -> dict[str, Any]:
    return {"run_id": row["run_id"], "set_id": row["set_id"], "split": row["split"], "model": row["model"],
            "prompt_versions": json.loads(row["prompt_versions_json"]), "replay_strategist": bool(row["replay_strategist"]),
            "backtest_run_id": row["backtest_run_id"], "scores": json.loads(row["scores_json"]),
            "item_scores": json.loads(row["item_scores_json"]), "created_at": row["created_at"]}
