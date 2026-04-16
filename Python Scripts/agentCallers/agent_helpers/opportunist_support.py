from __future__ import annotations

"""Shared article utilities for opportunist helper modules.

Sector and industry opportunist helpers both build the same article envelopes,
merge macro context, and filter already-processed article ids. Centralizing the
shared logic here keeps each stage helper focused on its own persistence rules.
"""

from typing import Any

from db_helpers import get_connection


def build_base_article_record(article: dict[str, Any], *, article_scope: str, sector_source: str) -> dict[str, Any]:
    """Normalize a sector-linked article into the common opportunist shape."""
    return {
        "article_id": int(article["article_id"]),
        "title": article.get("title") or "",
        "summary": article.get("summary") or "",
        "body": article.get("body") or "",
        "source": article.get("source") or "",
        "source_url": article.get("source_url") or "",
        "published_at": article.get("published_at") or "",
        "article_scope": article_scope,
        "sector_context": {
            "sector_source": sector_source,
        },
    }


def seed_sector_rss_articles(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Seed the working article map from sector RSS rows."""
    deduped_articles: dict[int, dict[str, Any]] = {}
    for article in payload.get("sector_rss_news", []):
        article_record = build_base_article_record(
            article,
            article_scope="sector_rss",
            sector_source="cnbc_rss",
        )
        deduped_articles[article_record["article_id"]] = article_record
    return deduped_articles


def merge_macro_articles(
    deduped_articles: dict[int, dict[str, Any]],
    payload: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    """Merge macro-news rows into an existing opportunist article map."""
    for article in payload.get("related_macro_news", []):
        article_id = int(article["article_id"])
        entry = deduped_articles.setdefault(
            article_id,
            build_base_article_record(
                article,
                article_scope="macro_news",
                sector_source="macro_news_high_confidence",
            ),
        )
        entry["macro_context"] = {
            "news_scope": article.get("news_scope") or "",
            "confidence": article.get("confidence") or "",
            "reason": article.get("reason") or "",
        }
        if entry.get("article_scope") == "sector_rss":
            entry["article_scope"] = "sector_rss_and_macro_news"

    return deduped_articles


def sort_articles_by_recency(
    articles: list[dict[str, Any]] | dict[int, dict[str, Any]],
    *,
    article_id_key: str = "article_id",
) -> list[dict[str, Any]]:
    """Return articles sorted newest-first for stable batching."""
    sortable_articles = articles.values() if isinstance(articles, dict) else articles
    return sorted(
        sortable_articles,
        key=lambda article: (
            str(article.get("published_at") or ""),
            int(article.get(article_id_key) or 0),
        ),
        reverse=True,
    )


def load_processed_article_ids(
    *,
    db_path: str,
    table_name: str,
    article_ids: list[int],
    company_id: int | None = None,
) -> set[int]:
    """Load processed article ids from one opportunist tracking table."""
    if not article_ids:
        return set()

    placeholders = ",".join("?" for _ in article_ids)
    where_clauses = [f"article_id IN ({placeholders})"]
    parameters: list[Any] = list(article_ids)

    if company_id is not None:
        where_clauses.insert(0, "company_id = ?")
        parameters.insert(0, int(company_id))

    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT article_id
            FROM {table_name}
            WHERE {" AND ".join(where_clauses)}
            """,
            tuple(parameters),
        ).fetchall()

    return {int(row["article_id"]) for row in rows}


def filter_unprocessed_articles(
    articles: list[dict[str, Any]],
    *,
    db_path: str,
    table_name: str,
    company_id: int | None = None,
    article_id_key: str = "article_id",
) -> list[dict[str, Any]]:
    """Remove articles that were already processed by an opportunist stage."""
    processed_article_ids = load_processed_article_ids(
        db_path=db_path,
        table_name=table_name,
        article_ids=[int(article[article_id_key]) for article in articles],
        company_id=company_id,
    )
    if not processed_article_ids:
        return articles

    return [
        article
        for article in articles
        if int(article[article_id_key]) not in processed_article_ids
    ]


def extract_impacts_from_payload(payload: Any, *, key: str = "impacts") -> list[dict[str, Any]] | None:
    """Extract the model-produced impact list from standard response shapes."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return None

    impacts = payload.get(key)
    if isinstance(impacts, list):
        return impacts

    for nested_key in ("output_schema", "required_output"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            nested_impacts = nested.get(key)
            if isinstance(nested_impacts, list):
                return nested_impacts

    return None
