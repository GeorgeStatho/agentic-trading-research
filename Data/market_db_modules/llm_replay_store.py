from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from db_common import get_connection, json_text
from services.option_dte_buckets import normalize_target_dte_bucket
from .bootstrap import initialize_database
from .candidate_lifecycle import stage_payloads
from .shared import normalize_timestamp


def list_frozen_manager_decisions(
    *,
    start_at: str | None = None,
    end_at: str | None = None,
    limit: int | None = None,
    db_path: Path | str | None = None,
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


def frozen_manager_input(decision: dict[str, Any]) -> dict[str, Any] | None:
    """The manager input frozen with a listed decision; None for decisions older than frozen inputs."""
    manager_input = decision.get("manager_input_json")
    if isinstance(manager_input, str):
        try:
            manager_input = json.loads(manager_input or "{}")
        except json.JSONDecodeError:
            return None
    payload = manager_input.get("replay_input") if isinstance(manager_input, dict) else None
    return payload if isinstance(payload, dict) and isinstance(payload.get("company"), dict) else None


@dataclass(frozen=True)
class FrozenDecision:
    """Everything production froze for one manager decision; replays read these, never the stores.

    `strategist_input` is the `pre_strategist` checkpoint. `saved_chain` is the option chain production
    re-fetched for its chosen DTE bucket just before selecting: the frozen manager input holds only the
    pre-decision chain. `manifest` is the pipeline manifest the candidate ran under. All three are None
    for a decision without a candidate.
    """
    decision: dict[str, Any]
    manager_input: dict[str, Any]
    strategist_input: dict[str, Any] | None = None
    saved_chain: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None

    @classmethod
    def load(cls, decision: dict[str, Any], *, db_path: Path | str | None = None) -> "FrozenDecision":
        """Raises SnapshotHashMismatch when a checkpoint's stored text no longer matches its hash."""
        manager_input = frozen_manager_input(decision)
        if manager_input is None:
            raise ValueError(f"Decision {decision.get('id')} has no frozen manager input")
        candidate_id = decision.get("candidate_id")
        if not candidate_id:
            return cls(decision, manager_input)
        stages = stage_payloads(candidate_id, ("pre_strategist", "pre_contract_selection"), db_path=db_path)
        strategist_input = stages.get("pre_strategist")
        with closing(get_connection(db_path)) as conn:
            row = conn.execute("SELECT pipeline_version_json FROM candidates WHERE candidate_id = ?",
                               (candidate_id,)).fetchone()
        manifest = json.loads(row["pipeline_version_json"] or "{}") if row is not None else {}
        return cls(decision, manager_input,
                   strategist_input if isinstance((strategist_input or {}).get("company"), dict) else None,
                   _directional_chain(stages.get("pre_contract_selection")), manifest or None)

    def bucket_chain(self, bucket: str) -> dict[str, Any] | None:
        """The saved option market when production selected from `bucket`, else None."""
        if self.saved_chain is None or normalize_target_dte_bucket(self.saved_chain["target_dte_bucket"]) != bucket:
            return None
        return self.saved_chain["option_market"]


def _directional_chain(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    recommendation = (payload or {}).get("recommendation") or {}
    option_market = ((payload or {}).get("market_context") or {}).get("option_market")
    if recommendation.get("decision") not in {"call", "put"} or not isinstance(option_market, dict):
        return None
    return {"target_dte_bucket": recommendation.get("target_dte_bucket"), "option_market": option_market}


def symbols_without_daily_bars(symbols: set[str], *, since: str, db_path: Path | str | None = None) -> set[str]:
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
    db_path: Path | str | None = None,
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
    db_path: Path | str | None = None,
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
