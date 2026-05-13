from __future__ import annotations

from db_helpers import DB_PATH, get_connection, validate_sql_identifier

from agent_pipeline.ranking import (
    DEFAULT_TOP_COMPANY_COUNT,
    DEFAULT_TOP_INDUSTRY_COUNT,
    DEFAULT_TOP_SECTOR_COUNT,
    RANKING_MAX_AGE_DAYS_UNSET,
    resolve_ranking_max_age_days,
)
from agent_pipeline.target_selection import get_current_pipeline_targets


def _count_rows_for_ids(
    conn,
    table_name: str,
    column_name: str,
    values: list[int],
) -> int:
    if not values:
        return 0
    safe_table_name = validate_sql_identifier(table_name)
    safe_column_name = validate_sql_identifier(column_name)
    placeholders = ",".join("?" for _ in values)
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM {safe_table_name} WHERE {safe_column_name} IN ({placeholders})",
        tuple(values),
    ).fetchone()
    return int(row["c"]) if row is not None else 0


def _load_article_ids_for_target_rows(
    conn,
    *,
    impact_table: str,
    id_column: str,
    target_ids: list[int],
) -> list[int]:
    # Load the article ids for target rows once so the downstream logic can stay focused on orchestration.
    if not target_ids:
        return []
    safe_impact_table = validate_sql_identifier(impact_table)
    safe_id_column = validate_sql_identifier(id_column)
    placeholders = ",".join("?" for _ in target_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT article_id
        FROM {safe_impact_table}
        WHERE {safe_id_column} IN ({placeholders})
        ORDER BY article_id
        """,
        tuple(target_ids),
    ).fetchall()
    return [int(row["article_id"]) for row in rows]


def clear_current_pipeline_targets(
    *,
    top_sector_count: int = DEFAULT_TOP_SECTOR_COUNT,
    top_industry_count: int = DEFAULT_TOP_INDUSTRY_COUNT,
    top_company_count: int = DEFAULT_TOP_COMPANY_COUNT,
    ranking_max_age_days: int | None | object = RANKING_MAX_AGE_DAYS_UNSET,
) -> dict[str, object]:
    """Delete persisted outputs for the currently ranked pipeline targets."""
    ranking_max_age_days = resolve_ranking_max_age_days(ranking_max_age_days)
    targets = get_current_pipeline_targets(
        top_sector_count=top_sector_count,
        top_industry_count=top_industry_count,
        top_company_count=top_company_count,
        ranking_max_age_days=ranking_max_age_days,
    )

    with get_connection(DB_PATH) as conn:
        sector_rows = conn.execute(
            """
            SELECT id, sector_key
            FROM sectors
            WHERE sector_key IN ({placeholders})
            ORDER BY id
            """.format(
                placeholders=",".join("?" for _ in targets["top_sector_keys"]) or "''"
            ),
            tuple(targets["top_sector_keys"]),
        ).fetchall() if targets["top_sector_keys"] else []
        sector_ids = [int(row["id"]) for row in sector_rows]

        industry_rows = conn.execute(
            """
            SELECT id, industry_key
            FROM industries
            WHERE industry_key IN ({placeholders})
            ORDER BY id
            """.format(
                placeholders=",".join("?" for _ in targets["top_industry_keys"]) or "''"
            ),
            tuple(targets["top_industry_keys"]),
        ).fetchall() if targets["top_industry_keys"] else []
        industry_ids = [int(row["id"]) for row in industry_rows]

        company_ids = [int(company_id) for company_id in targets["selected_company_ids"]]

        sector_article_ids = _load_article_ids_for_target_rows(
            conn,
            impact_table="sector_opportunist_impacts",
            id_column="sector_id",
            target_ids=sector_ids,
        )
        industry_article_ids = _load_article_ids_for_target_rows(
            conn,
            impact_table="industry_opportunist_impacts",
            id_column="industry_id",
            target_ids=industry_ids,
        )

        deleted_counts = {
            "sector_opportunist_article_processing": _count_rows_for_ids(
                conn,
                "sector_opportunist_article_processing",
                "article_id",
                sector_article_ids,
            ),
            "sector_opportunist_impacts": _count_rows_for_ids(
                conn,
                "sector_opportunist_impacts",
                "sector_id",
                sector_ids,
            ),
            "industry_opportunist_article_processing": _count_rows_for_ids(
                conn,
                "industry_opportunist_article_processing",
                "article_id",
                industry_article_ids,
            ),
            "industry_opportunist_impacts": _count_rows_for_ids(
                conn,
                "industry_opportunist_impacts",
                "industry_id",
                industry_ids,
            ),
            "company_opportunist_article_processing": _count_rows_for_ids(
                conn,
                "company_opportunist_article_processing",
                "company_id",
                company_ids,
            ),
            "company_opportunist_impacts": _count_rows_for_ids(
                conn,
                "company_opportunist_impacts",
                "company_id",
                company_ids,
            ),
        }

        if sector_article_ids:
            placeholders = ",".join("?" for _ in sector_article_ids)
            conn.execute(
                f"DELETE FROM sector_opportunist_article_processing WHERE article_id IN ({placeholders})",
                tuple(sector_article_ids),
            )
        if sector_ids:
            placeholders = ",".join("?" for _ in sector_ids)
            conn.execute(
                f"DELETE FROM sector_opportunist_impacts WHERE sector_id IN ({placeholders})",
                tuple(sector_ids),
            )

        if industry_article_ids:
            placeholders = ",".join("?" for _ in industry_article_ids)
            conn.execute(
                f"DELETE FROM industry_opportunist_article_processing WHERE article_id IN ({placeholders})",
                tuple(industry_article_ids),
            )
        if industry_ids:
            placeholders = ",".join("?" for _ in industry_ids)
            conn.execute(
                f"DELETE FROM industry_opportunist_impacts WHERE industry_id IN ({placeholders})",
                tuple(industry_ids),
            )

        if company_ids:
            placeholders = ",".join("?" for _ in company_ids)
            conn.execute(
                f"DELETE FROM company_opportunist_article_processing WHERE company_id IN ({placeholders})",
                tuple(company_ids),
            )
            conn.execute(
                f"DELETE FROM company_opportunist_impacts WHERE company_id IN ({placeholders})",
                tuple(company_ids),
            )

    return {
        "targets": targets,
        "deleted_counts": deleted_counts,
    }
