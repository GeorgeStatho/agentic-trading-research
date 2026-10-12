from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
from typing import Any

from db_common import DB_PATH, get_connection, json_text
from .bootstrap import initialize_database
from .shared import normalize_timestamp


def list_frozen_manager_decisions(
    *,
    start_at: str | None = None,
    end_at: str | None = None,
    limit: int | None = None,
    db_path: Path | str = DB_PATH,
) -> list[dict[str, Any]]:
    """Every stored manager decision with its frozen input, oldest first.

    Unlike the replay evaluator's source, this includes decisions that never traded:
    a replayed model may trade where production did not.
    """
    initialize_database(db_path=db_path)
    query = """
        SELECT history.id, history.symbol, history.decision_run_at, history.manager_decision, history.candidate_id,
               payload.manager_input_json, payload.manager_output_json,
               COALESCE(sector.sector_key, 'unknown') AS sector_key
        FROM manager_decision_history AS history
        JOIN manager_decision_payloads AS payload ON payload.manager_decision_history_id = history.id
        LEFT JOIN companies AS company ON company.id = history.company_id
        LEFT JOIN industries AS industry ON industry.id = company.industry_id
        LEFT JOIN sectors AS sector ON sector.id = industry.sector_id
        WHERE 1 = 1
    """
    parameters: list[Any] = []
    if start_at:
        query += " AND history.decision_run_at >= ?"
        parameters.append(normalize_timestamp(start_at))
    if end_at:
        query += " AND history.decision_run_at <= ?"
        parameters.append(normalize_timestamp(end_at))
    # Limit the most recent window first, then return it in chronological order.
    query += " ORDER BY history.decision_run_at DESC, history.id DESC"
    if limit is not None:
        query += " LIMIT ?"
        parameters.append(max(1, int(limit)))
    with closing(get_connection(db_path)) as conn:
        rows = [dict(row) for row in conn.execute(query, tuple(parameters)).fetchall()]
    return rows[::-1]


def get_saved_bucket_chain(candidate_id: str | None, *, db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    """The option chain production re-fetched for its chosen DTE bucket, just before selecting.

    The frozen manager input holds only the pre-decision chain, so this is the only record of
    the chain a directional decision was selected from. None when production never fetched one.
    """
    if not candidate_id:
        return None
    with closing(get_connection(db_path)) as conn:
        row = conn.execute(
            """
            SELECT snapshot_json FROM candidate_snapshots
            WHERE candidate_id = ? AND stage = 'pre_contract_selection'
            ORDER BY id DESC LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()
    if row is None:
        return None
    payload = json.loads(row["snapshot_json"]).get("payload") or {}
    recommendation = payload.get("recommendation") or {}
    option_market = (payload.get("market_context") or {}).get("option_market")
    if recommendation.get("decision") not in {"call", "put"} or not isinstance(option_market, dict):
        return None
    return {"target_dte_bucket": recommendation.get("target_dte_bucket"), "option_market": option_market}


def get_frozen_strategist_input(candidate_id: str | None, *, db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    """The input production's strategist answered for this candidate, or None when it has no completed run."""
    if not candidate_id:
        return None
    with closing(get_connection(db_path)) as conn:
        row = conn.execute(
            """
            SELECT input_payload_json FROM strategist_runs
            WHERE candidate_id = ? AND status = 'completed'
            ORDER BY id DESC LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()
    payload = json.loads(row["input_payload_json"] or "{}") if row is not None else {}
    return payload if isinstance(payload.get("company"), dict) else None


def get_candidate_manifest(candidate_id: str | None, *, db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    """The pipeline manifest a candidate ran under: production's settings, not this checkout's."""
    if not candidate_id:
        return None
    with closing(get_connection(db_path)) as conn:
        row = conn.execute("SELECT pipeline_version_json FROM candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
    manifest = json.loads(row["pipeline_version_json"] or "{}") if row is not None else {}
    return manifest or None


def symbols_without_daily_bars(symbols: set[str], *, since: str, db_path: Path | str = DB_PATH) -> set[str]:
    """Symbols with no stored daily close on or after `since`."""
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn:
        covered = {row["symbol"] for row in conn.execute(
            f"""
            SELECT DISTINCT symbol FROM historical_underlying_bars
            WHERE timeframe = '1d' AND close_price > 0 AND bar_at >= ?
              AND symbol IN ({",".join("?" for _ in symbols)})
            """,
            (normalize_timestamp(since), *symbols),
        )} if symbols else set()
    return set(symbols) - covered


def get_cached_llm_replay(
    *,
    stage: str,
    provider: str,
    model: str,
    prompt_version: str,
    input_hash: str,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any] | None:
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn:
        row = conn.execute(
            """
            SELECT output_json FROM llm_replay_cache
            WHERE stage = ? AND provider = ? AND model = ? AND prompt_version = ? AND input_hash = ?
            """,
            (stage, provider, model, prompt_version, input_hash),
        ).fetchone()
    return json.loads(row["output_json"]) if row is not None else None


def store_cached_llm_replay(
    *,
    stage: str,
    provider: str,
    model: str,
    prompt_version: str,
    input_hash: str,
    output: dict[str, Any],
    db_path: Path | str = DB_PATH,
) -> None:
    initialize_database(db_path=db_path)
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO llm_replay_cache (stage, provider, model, prompt_version, input_hash, output_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (stage, provider, model, prompt_version, input_hash, json_text(output) or "{}"),
        )
