from __future__ import annotations

import re
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, cast
from urllib.parse import urlsplit

from core.CommonPipeline import (
    MAX_ARTICLE_AGE_DAYS,
    clear_failed_url,
    compute_article_scores,
    fetch_existing_article_by_url,
    is_recent_article,
    record_failed_url,
)
from engine.article_extraction import ArticleExtractionResult
from processing.news_normalization import build_content_hash, normalize_title, normalize_url
from sources.source_config import get_max_article_age_days, get_source_metadata, is_allowed_source
from db_common import get_connection
from news_db_modules.article_store import add_news_article

CNBc_URL_DATE_RE = re.compile(r"/(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/")

def _extract_cnbc_url_published_at(url: str) -> str | None:
    lowered = str(url or "").strip().lower()
    if "cnbc.com" not in lowered:
        return None

    match = CNBc_URL_DATE_RE.search(urlsplit(lowered).path)
    if match is None:
        return None

    try:
        published_at = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None

    return published_at.isoformat()


def _should_skip_article_fetch_for_age(url: str, *, max_age_days: int) -> bool:
    inferred_published_at = _extract_cnbc_url_published_at(url)
    if not inferred_published_at:
        return False
    return not is_recent_article(inferred_published_at, max_age_days=max_age_days)


def collect_article_urls_to_fetch(
    candidate_links: list[dict],
    max_articles: int,
    *,
    max_age_days: int = MAX_ARTICLE_AGE_DAYS,
    should_include_link: Callable[[str, dict], bool] | None = None,
) -> list[str]:
    # Collect the article urls to fetch into one cleaned result for the next step.
    urls_to_fetch: list[str] = []
    seen_urls: set[str] = set()

    for link in candidate_links:
        if len(urls_to_fetch) >= max_articles:
            break

        href = str(link.get("href") or "").strip()
        if not href:
            continue
        if should_include_link is not None and not should_include_link(href, link):
            continue
        if not is_allowed_source(href):
            continue
        article_max_age_days = get_max_article_age_days(href, max_age_days)
        if _should_skip_article_fetch_for_age(href, max_age_days=article_max_age_days):
            continue
        if fetch_existing_article_by_url(href) is not None:
            continue
        if href in seen_urls:
            continue

        urls_to_fetch.append(href)
        seen_urls.add(href)

    return urls_to_fetch


@dataclass(frozen=True)
class NewsEntity:
    kind: str  # "company" | "industry" | "sector" | "us" | "world"
    id: int
    label: str
    identity: dict
    link: Callable[..., int]  # called as link(article_id=, source_page_url=, conn=)


def _persist(entity: NewsEntity, source_page_url: str, article_id: int | None = None, **article: Any) -> None:
    # Insert the article unless it already exists, then link it to the entity in the same transaction.
    with closing(get_connection()) as conn, conn:
        if article_id is None:
            article_id = add_news_article(**article, conn=conn)
        entity.link(article_id=article_id, source_page_url=source_page_url, conn=conn)


def save_followed_article_links(
    *,
    source_page_url: str,
    candidate_links: list[dict],
    entity: NewsEntity,
    max_articles: int,
    fetched_articles: dict[str, ArticleExtractionResult],
    logger,
    should_include_link: Callable[[str, dict], bool] | None = None,
    max_age_days: int = MAX_ARTICLE_AGE_DAYS,
) -> int:
    # Link reused articles and insert new ones (with scoring and raw_json) for one entity.
    saved_count = 0
    normalized_fetched_articles: dict[str, ArticleExtractionResult] = {}
    for fetched_url, fetched_article in fetched_articles.items():
        normalized_fetched_url = normalize_url(fetched_url)
        if normalized_fetched_url and normalized_fetched_url not in normalized_fetched_articles:
            normalized_fetched_articles[normalized_fetched_url] = fetched_article

    logger.info(
        "Processing %s candidate article links for %s %s from %s",
        len(candidate_links),
        entity.kind,
        entity.label,
        source_page_url,
    )

    for link in candidate_links:
        if saved_count >= max_articles:
            logger.info(
                "Reached article limit of %s for %s %s from %s",
                max_articles,
                entity.kind,
                entity.label,
                source_page_url,
            )
            break

        href = str(link.get("href") or "").strip()
        if not href:
            continue
        if should_include_link is not None and not should_include_link(href, link):
            continue
        if not is_allowed_source(href):
            logger.info("Skipping disallowed article URL %s for %s %s", href, entity.kind, entity.label)
            continue

        article_max_age_days = get_max_article_age_days(href, max_age_days)
        existing_article = fetch_existing_article_by_url(href)

        if existing_article is not None:
            existing_published_at = existing_article.get("published_at")
            if existing_published_at is not None and not isinstance(existing_published_at, str):
                existing_published_at = str(existing_published_at)
            if not is_recent_article(existing_published_at, max_age_days=article_max_age_days):
                logger.info("Skipping stale existing article %s for %s %s", href, entity.kind, entity.label)
                continue

            _persist(entity, source_page_url, article_id=existing_article["id"])
            logger.info("Reusing existing article %s for %s %s", href, entity.kind, entity.label)
        else:
            article = cast(ArticleExtractionResult | None, fetched_articles.get(href))
            if article is None:
                article = cast(
                    ArticleExtractionResult | None,
                    normalized_fetched_articles.get(normalize_url(href)),
                )
            if article is None:
                logger.warning(
                    "Missing crawled article result for %s %s at %s",
                    entity.kind,
                    entity.label,
                    href,
                )
                continue
            if not article.success:
                record_failed_url(href, "article_follow", article.error)
                logger.warning(
                    "Article follow failed for %s %s at %s: %s",
                    entity.kind,
                    entity.label,
                    href,
                    article.error,
                )
                continue
            clear_failed_url(href)
            if not is_recent_article(article.published_at, max_age_days=article_max_age_days):
                logger.info("Skipping stale fetched article %s for %s %s", href, entity.kind, entity.label)
                continue

            normalized_href = normalize_url(href)
            title = article.title or link.get("text") or href
            source_metadata = get_source_metadata(href)
            score_bundle = compute_article_scores(article, link, href, source_metadata)
            raw_json = {
                **entity.identity,
                "source_page_url": source_page_url,
                "link": link,
                "normalized_url": normalized_href,
                "normalized_title": normalize_title(title),
                "content_hash": build_content_hash(article.text),
                "source_metadata": source_metadata,
                "scores": score_bundle,
                "extracted_article": {
                    "url": article.url,
                    "title": article.title,
                    "text": article.text,
                    "published_at": article.published_at,
                    "success": article.success,
                    "error": article.error,
                    "reused_existing_article": False,
                },
            }
            _persist(
                entity,
                source_page_url,
                source=source_metadata["domain"],
                article_key=normalized_href or href,
                title=title,
                source_url=href,
                summary=link.get("text"),
                body=article.text,
                published_at=article.published_at or None,
                raw_json=raw_json,
                **score_bundle,
            )
            logger.info("Fetched new article %s for %s %s", href, entity.kind, entity.label)

        saved_count += 1
        logger.info(
            "Saved article %s for %s %s (%s/%s saved for this page)",
            href,
            entity.kind,
            entity.label,
            saved_count,
            max_articles,
        )

    return saved_count
