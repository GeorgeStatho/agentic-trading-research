from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from urllib.parse import quote_plus

DATA_DIR = Path(__file__).resolve().parents[3] / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

from db_common import get_connection
from news_db import add_failed_url, get_failed_url, remove_failed_url
from news_normalization import normalize_url
from scoring import (
    compute_directness_score,
    compute_evidence_score,
    compute_factuality_score,
    confirmation_score,
    recency_score,
)
from core.scrape_logging import get_scrape_logger
from source_config import get_article_patterns, is_allowed_source


MAX_ARTICLES_PER_SEARCH_PAGE = 10
MAX_ARTICLE_AGE_DAYS = 14
LOGGER = get_scrape_logger("common_pipeline")


def filter_article_links(page_url: str, links: list[dict], skip_existing: bool = True) -> list[dict]:
    if not is_allowed_source(page_url):
        LOGGER.warning("Skipping source page %s because the source is not allowed", page_url)
        return []

    patterns = get_article_patterns(page_url)

    filtered_links: list[dict] = []
    seen_hrefs: set[str] = set()
    for link in links:
        href = link.get("href", "")
        if not href or href in seen_hrefs:
            continue
        if not is_allowed_source(href):
            continue
        if patterns and not any(pattern in href for pattern in patterns):
            continue
        if should_skip_failed_url(href):
            LOGGER.info("Skipping previously failed URL %s discovered from %s", href, page_url)
            continue
        if skip_existing and fetch_existing_article_by_url(href) is not None:
            LOGGER.info("Skipping already-saved article URL %s discovered from %s", href, page_url)
            continue
        filtered_links.append(link)
        seen_hrefs.add(href)

    return filtered_links


def is_recent_article(published_at: str | None, max_age_days: int = MAX_ARTICLE_AGE_DAYS) -> bool:
    if not published_at:
        return False

    try:
        parsed = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return False

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return parsed >= cutoff


def article_age_days(published_at: str | None) -> float | None:
    if not published_at:
        return None

    try:
        parsed = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    delta = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    return max(0.0, delta.total_seconds() / 86400.0)


def compute_article_scores(article, link: dict, href: str, source_metadata: dict) -> dict:
    age_days = article_age_days(article.published_at)
    recency_value = recency_score(age_days) if age_days is not None else 0.0
    source_reputation_value = float(source_metadata.get("reputation_score") or 0.0)
    directness_value = compute_directness_score(
        title=article.title or link.get("text"),
        body=article.text,
        summary=link.get("text"),
        source_url=href,
        source_metadata=source_metadata,
    )
    factuality_value = compute_factuality_score(
        title=article.title or link.get("text"),
        body=article.text,
        summary=link.get("text"),
        source_url=href,
        source_metadata=source_metadata,
    )
    confirmation_value = confirmation_score(1)
    evidence_value = compute_evidence_score(
        recency=recency_value,
        source_reputation=source_reputation_value,
        directness=directness_value,
        confirmation=confirmation_value,
        factuality=factuality_value,
    )

    return {
        "age_days": age_days,
        "recency_score": recency_value,
        "source_reputation_score": source_reputation_value,
        "directness_score": directness_value,
        "confirmation_score": confirmation_value,
        "independent_source_count": 1,
        "factuality_score": factuality_value,
        "evidence_score": evidence_value,
    }


def build_source_url(search_term: str, source_config: dict) -> str:
    encoded_search_term = quote_plus(search_term)
    base_url = source_config["url"]
    if "{query}" in base_url:
        return base_url.format(query=encoded_search_term)
    return base_url


def fetch_existing_article_by_url(url: str) -> dict | None:
    normalized = normalize_url(url)
    if not normalized:
        return None

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                id,
                source,
                article_key,
                title,
                normalized_title,
                summary,
                body,
                normalized_url,
                content_hash,
                published_at,
                section,
                age_days,
                recency_score,
                source_reputation_score,
                directness_score,
                confirmation_score,
                independent_source_count,
                factuality_score,
                evidence_score,
                source_url
            FROM news_articles
            WHERE normalized_url = ?
            LIMIT 1
            """,
            (normalized,),
        ).fetchone()

    return dict(row) if row is not None else None


def _is_permanent_failure(error: str | None) -> bool:
    if not error:
        return False
    normalized_error = error.lower()
    return "http 403" in normalized_error or "http 404" in normalized_error


def should_skip_failed_url(url: str) -> bool:
    failed_row = get_failed_url(url)
    if failed_row is None:
        return False
    if int(failed_row.get("is_permanent") or 0) == 1:
        return True
    return int(failed_row.get("failure_count") or 0) >= 3


def record_failed_url(url: str, stage: str, error: str | None) -> None:
    LOGGER.warning("Recording failed URL %s at stage %s: %s", url, stage, error or "")
    add_failed_url(
        url,
        stage=stage,
        error=error,
        is_permanent=_is_permanent_failure(error),
    )


def clear_failed_url(url: str) -> None:
    LOGGER.info("Clearing failed URL record for %s after success", url)
    remove_failed_url(url)
