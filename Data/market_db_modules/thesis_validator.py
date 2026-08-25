from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from db_common import DB_PATH, clean_text, coerce_float, get_connection, json_text
from .bootstrap import initialize_database
from .shared import coerce_int, normalize_timestamp


def _parse_json_column(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _thesis_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    thesis = dict(row)
    thesis["source_article_ids"] = _parse_json_column(thesis.pop("source_article_ids_json", None), [])
    thesis["invalidation_conditions"] = _parse_json_column(thesis.pop("invalidation_conditions_json", None), [])
    return thesis


def _run_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    run = dict(row)
    run["evidence_pack"] = _parse_json_column(run.pop("evidence_pack_json", None), {})
    run["raw_samples"] = _parse_json_column(run.pop("raw_samples_json", None), [])
    run["evidence_ids"] = _parse_json_column(run.pop("evidence_ids_json", None), [])
    return run


def record_position_thesis(
    thesis: dict[str, Any],
    *,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> int | None:
    """Persist a Phase 0 thesis record; idempotent per open option symbol."""
    if not isinstance(thesis, dict):
        return None

    if conn is None:
        initialize_database(db_path=db_path)
        with get_connection(db_path) as local_conn:
            return record_position_thesis(thesis, conn=local_conn)

    option_symbol = clean_text(thesis.get("option_symbol"))
    symbol = clean_text(thesis.get("symbol"))
    opened_at = normalize_timestamp(thesis.get("opened_at"))
    if option_symbol is None or symbol is None:
        return None

    existing = conn.execute(
        """
        SELECT id
        FROM position_theses
        WHERE option_symbol = ? AND status = 'open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (option_symbol.upper(),),
    ).fetchone()
    if existing is not None:
        return int(existing["id"])

    cursor = conn.execute(
        """
        INSERT INTO position_theses (
            manager_decision_history_id,
            trade_execution_order_id,
            company_id,
            symbol,
            option_symbol,
            structure,
            opened_at,
            catalyst_type,
            catalyst_summary,
            source_article_ids_json,
            expected_horizon_days,
            invalidation_conditions_json,
            status,
            raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """,
        (
            coerce_int(thesis.get("manager_decision_history_id")),
            clean_text(thesis.get("trade_execution_order_id")),
            coerce_int(thesis.get("company_id")),
            symbol.upper(),
            option_symbol.upper(),
            clean_text(thesis.get("structure")),
            opened_at,
            clean_text(thesis.get("catalyst_type")),
            clean_text(thesis.get("catalyst_summary")),
            json_text(thesis.get("source_article_ids") or []) or "[]",
            coerce_int(thesis.get("expected_horizon_days")),
            json_text(thesis.get("invalidation_conditions") or []) or "[]",
            json_text(thesis.get("raw_json")) if thesis.get("raw_json") is not None else None,
        ),
    )
    return int(cursor.lastrowid)


def get_open_position_thesis_for_option_symbol(
    option_symbol: str,
    *,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    normalized_symbol = clean_text(option_symbol)
    if normalized_symbol is None:
        return None

    if conn is None:
        initialize_database(db_path=db_path)
        with get_connection(db_path) as local_conn:
            return get_open_position_thesis_for_option_symbol(normalized_symbol, conn=local_conn)

    row = conn.execute(
        """
        SELECT *
        FROM position_theses
        WHERE option_symbol = ? AND status = 'open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (normalized_symbol.upper(),),
    ).fetchone()
    return _thesis_row_to_dict(row) if row is not None else None


def close_position_thesis(
    option_symbol: str,
    *,
    closed_at: Any = None,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> bool:
    normalized_symbol = clean_text(option_symbol)
    if normalized_symbol is None:
        return False

    if conn is None:
        initialize_database(db_path=db_path)
        with get_connection(db_path) as local_conn:
            return close_position_thesis(normalized_symbol, closed_at=closed_at, conn=local_conn)

    cursor = conn.execute(
        """
        UPDATE position_theses
        SET status = 'closed', closed_at = ?
        WHERE option_symbol = ? AND status = 'open'
        """,
        (normalize_timestamp(closed_at), normalized_symbol.upper()),
    )
    return int(cursor.rowcount or 0) > 0


def record_thesis_validation_run(
    run: dict[str, Any],
    *,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> int | None:
    """Persist one adjudication run; unique per (thesis_id, run_date)."""
    if not isinstance(run, dict):
        return None

    if conn is None:
        initialize_database(db_path=db_path)
        with get_connection(db_path) as local_conn:
            return record_thesis_validation_run(run, conn=local_conn)

    thesis_id = coerce_int(run.get("thesis_id"))
    option_symbol = clean_text(run.get("option_symbol"))
    thesis_state = clean_text(run.get("thesis_state")) or "unclear"
    run_at = normalize_timestamp(run.get("run_at"))
    run_date = clean_text(run.get("run_date")) or str(run_at or "")[:10]
    if thesis_id is None or option_symbol is None or not run_date:
        return None

    try:
        cursor = conn.execute(
            """
            INSERT INTO thesis_validation_runs (
                thesis_id,
                option_symbol,
                symbol,
                run_at,
                run_date,
                mode,
                evidence_pack_json,
                raw_samples_json,
                sample_count,
                consistency,
                flip_detected,
                grounded,
                thesis_state,
                would_open_today,
                evidence_ids_json,
                rationale,
                confidence,
                rules_verdict,
                arbiter_action,
                arbiter_reason,
                executed,
                mark_mid_price,
                error_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                thesis_id,
                option_symbol.upper(),
                clean_text(run.get("symbol")),
                run_at,
                run_date,
                clean_text(run.get("mode")) or "shadow",
                json_text(run.get("evidence_pack") or {}) or "{}",
                json_text(run.get("raw_samples") or []) or "[]",
                coerce_int(run.get("sample_count")) or 0,
                clean_text(run.get("consistency")),
                1 if run.get("flip_detected") else 0,
                1 if run.get("grounded") else 0,
                thesis_state,
                clean_text(run.get("would_open_today")),
                json_text(run.get("evidence_ids") or []) or "[]",
                clean_text(run.get("rationale")),
                coerce_float(run.get("confidence")),
                clean_text(run.get("rules_verdict")),
                clean_text(run.get("arbiter_action")) or "none",
                clean_text(run.get("arbiter_reason")),
                1 if run.get("executed") else 0,
                coerce_float(run.get("mark_mid_price")),
                clean_text(run.get("error_text")),
            ),
        )
    except sqlite3.IntegrityError:
        return None
    return int(cursor.lastrowid)


def get_thesis_validation_run_for_date(
    *,
    thesis_id: int,
    run_date: str,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    normalized_id = coerce_int(thesis_id)
    normalized_date = clean_text(run_date)
    if normalized_id is None or normalized_date is None:
        return None

    if conn is None:
        initialize_database(db_path=db_path)
        with get_connection(db_path) as local_conn:
            return get_thesis_validation_run_for_date(
                thesis_id=normalized_id,
                run_date=normalized_date,
                conn=local_conn,
            )

    row = conn.execute(
        "SELECT * FROM thesis_validation_runs WHERE thesis_id = ? AND run_date = ?",
        (normalized_id, normalized_date),
    ).fetchone()
    return _run_row_to_dict(row) if row is not None else None


def count_thesis_validator_exits_for_date(
    run_date: str,
    *,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> int:
    normalized_date = clean_text(run_date)
    if normalized_date is None:
        return 0

    if conn is None:
        initialize_database(db_path=db_path)
        with get_connection(db_path) as local_conn:
            return count_thesis_validator_exits_for_date(normalized_date, conn=local_conn)

    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM thesis_validation_runs
        WHERE run_date = ?
          AND executed = 1
          AND arbiter_action IN ('close', 'close_half')
        """,
        (normalized_date,),
    ).fetchone()
    return int((row["count"] if row is not None else 0) or 0)


def list_thesis_validation_runs(
    *,
    limit: int = 200,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    if conn is None:
        initialize_database(db_path=db_path)
        with get_connection(db_path) as local_conn:
            return list_thesis_validation_runs(limit=limit, conn=local_conn)

    rows = conn.execute(
        """
        SELECT *
        FROM thesis_validation_runs
        ORDER BY run_at DESC, id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [_run_row_to_dict(row) for row in rows]


def get_latest_thesis_validation_runs_by_option_symbol(
    *,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> dict[str, dict[str, Any]]:
    """Latest run per option symbol, without the heavy evidence/sample blobs."""
    if conn is None:
        initialize_database(db_path=db_path)
        with get_connection(db_path) as local_conn:
            return get_latest_thesis_validation_runs_by_option_symbol(conn=local_conn)

    rows = conn.execute(
        """
        SELECT
            runs.thesis_id,
            runs.option_symbol,
            runs.symbol,
            runs.run_at,
            runs.run_date,
            runs.mode,
            runs.consistency,
            runs.flip_detected,
            runs.grounded,
            runs.thesis_state,
            runs.would_open_today,
            runs.rationale,
            runs.confidence,
            runs.rules_verdict,
            runs.arbiter_action,
            runs.arbiter_reason,
            runs.executed,
            runs.mark_mid_price
        FROM thesis_validation_runs AS runs
        JOIN (
            SELECT option_symbol, MAX(run_at) AS latest_run_at
            FROM thesis_validation_runs
            GROUP BY option_symbol
        ) AS latest
            ON latest.option_symbol = runs.option_symbol
           AND latest.latest_run_at = runs.run_at
        """
    ).fetchall()
    return {str(row["option_symbol"]): dict(row) for row in rows}


def summarize_thesis_validator_health(
    *,
    max_age_days: int = 60,
    db_path: Path | str = DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Shadow-promotion health metrics: flip rate, action rate, grounding rate."""
    if conn is None:
        initialize_database(db_path=db_path)
        with get_connection(db_path) as local_conn:
            return summarize_thesis_validator_health(max_age_days=max_age_days, conn=local_conn)

    rows = conn.execute(
        """
        SELECT
            run_date,
            consistency,
            flip_detected,
            grounded,
            thesis_state,
            arbiter_action,
            executed,
            error_text
        FROM thesis_validation_runs
        WHERE run_date >= date('now', ?)
        """,
        (f"-{max(1, int(max_age_days))} days",),
    ).fetchall()

    evaluated = [row for row in rows if str(row["consistency"] or "") not in {"", "not_evaluated"}]
    evaluated_count = len(evaluated)
    flip_count = sum(1 for row in evaluated if row["flip_detected"])
    grounded_count = sum(1 for row in evaluated if row["grounded"])
    action_count = sum(
        1 for row in evaluated if str(row["arbiter_action"] or "none") in {"close", "close_half", "flag"}
    )
    state_counts: dict[str, int] = {}
    for row in evaluated:
        state = str(row["thesis_state"] or "unclear")
        state_counts[state] = state_counts.get(state, 0) + 1

    return {
        "window_days": max(1, int(max_age_days)),
        "position_day_count": len(rows),
        "evaluated_count": evaluated_count,
        "trading_day_count": len({str(row["run_date"]) for row in rows}),
        "flip_rate": round(flip_count / evaluated_count, 4) if evaluated_count else None,
        "action_rate": round(action_count / evaluated_count, 4) if evaluated_count else None,
        "grounding_rate": round(grounded_count / evaluated_count, 4) if evaluated_count else None,
        "error_count": sum(1 for row in rows if str(row["error_text"] or "").strip()),
        "executed_exit_count": sum(
            1 for row in rows if row["executed"] and str(row["arbiter_action"] or "") in {"close", "close_half"}
        ),
        "thesis_state_counts": state_counts,
    }
