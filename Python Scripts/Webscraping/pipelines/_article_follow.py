from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from pipelines._shared import (
    ArticleExtractionResult,
    MAX_ARTICLE_AGE_DAYS,
    build_content_hash,
    clear_failed_url,
    compute_article_scores,
    crawl_article_pages,
    fetch_existing_article_by_url,
    get_max_article_age_days,
    get_source_metadata,
    is_allowed_source,
    is_recent_article,
    normalize_title,
    normalize_url,
    record_failed_url,
    cast,
)

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


def save_followed_article_links(
    *,
    source_page_url: str,
    candidate_links: list[dict],
    entity: dict,
    entity_kind: str,
    entity_label: str,
    logger,
    save_article: Callable[[dict[str, Any]], Any],
    build_raw_json: Callable[[dict[str, Any]], dict[str, Any]],
    max_articles: int,
    max_age_days: int = MAX_ARTICLE_AGE_DAYS,
    fetched_articles: dict[str, ArticleExtractionResult] | None = None,
    should_include_link: Callable[[str, dict], bool] | None = None,
) -> int:
    saved_count = 0
    fetched_articles = dict(fetched_articles or {})
    normalized_fetched_articles: dict[str, ArticleExtractionResult] = {}
    for fetched_url, fetched_article in fetched_articles.items():
        normalized_fetched_url = normalize_url(fetched_url)
        if normalized_fetched_url and normalized_fetched_url not in normalized_fetched_articles:
            normalized_fetched_articles[normalized_fetched_url] = fetched_article

    logger.info(
        "Processing %s candidate article links for %s %s from %s",
        len(candidate_links),
        entity_kind,
        entity_label,
        source_page_url,
    )

    if not fetched_articles:
        urls_to_fetch = collect_article_urls_to_fetch(
            candidate_links,
            max_articles,
            max_age_days=max_age_days,
            should_include_link=should_include_link,
        )
        if urls_to_fetch:
            logger.info(
                "Fetching %s article pages through Scrapy for %s %s",
                len(urls_to_fetch),
                entity_kind,
                entity_label,
            )
            fetched_articles = crawl_article_pages(urls_to_fetch)

    for link in candidate_links:
        if saved_count >= max_articles:
            logger.info(
                "Reached article limit of %s for %s %s from %s",
                max_articles,
                entity_kind,
                entity_label,
                source_page_url,
            )
            break

        href = str(link.get("href") or "").strip()
        if not href:
            continue
        if should_include_link is not None and not should_include_link(href, link):
            continue
        if not is_allowed_source(href):
            logger.info("Skipping disallowed article URL %s for %s %s", href, entity_kind, entity_label)
            continue

        article_max_age_days = get_max_article_age_days(href, max_age_days)
        normalized_href = normalize_url(href)
        existing_article = fetch_existing_article_by_url(href)

        if existing_article is not None:
            existing_article_id = existing_article.get("id")
            existing_published_at = existing_article.get("published_at")
            if existing_published_at is not None and not isinstance(existing_published_at, str):
                existing_published_at = str(existing_published_at)
            if not is_recent_article(existing_published_at, max_age_days=article_max_age_days):
                logger.info("Skipping stale existing article %s for %s %s", href, entity_kind, entity_label)
                continue

            title = existing_article.get("title") or link.get("text") or href
            article_key = existing_article.get("article_key") or normalized_href or href
            source_metadata = get_source_metadata(existing_article.get("source_url") or href)
            source = existing_article.get("source") or source_metadata["domain"]
            normalized_article_title = existing_article.get("normalized_title") or normalize_title(title)
            content_hash = existing_article.get("content_hash") or build_content_hash(existing_article.get("body") or "")
            score_bundle = {
                "age_days": existing_article.get("age_days"),
                "recency_score": existing_article.get("recency_score"),
                "source_reputation_score": existing_article.get("source_reputation_score"),
                "directness_score": existing_article.get("directness_score"),
                "confirmation_score": existing_article.get("confirmation_score"),
                "independent_source_count": existing_article.get("independent_source_count"),
                "factuality_score": existing_article.get("factuality_score"),
                "evidence_score": existing_article.get("evidence_score"),
            }
            body = existing_article.get("body")
            published_at = existing_published_at
            extracted_article_payload = {
                "url": existing_article.get("source_url") or href,
                "title": existing_article.get("title"),
                "text": existing_article.get("body"),
                "published_at": existing_published_at,
                "success": True,
                "error": "",
                "reused_existing_article": True,
            }
            logger.info("Reusing existing article %s for %s %s", href, entity_kind, entity_label)
        else:
            existing_article_id = None
            article = cast(ArticleExtractionResult | None, fetched_articles.get(href))
            if article is None:
                article = cast(
                    ArticleExtractionResult | None,
                    normalized_fetched_articles.get(normalize_url(href)),
                )
            if article is None:
                logger.warning(
                    "Missing crawled article result for %s %s at %s",
                    entity_kind,
                    entity_label,
                    href,
                )
                continue
            if not article.success:
                record_failed_url(href, "article_follow", article.error)
                logger.warning(
                    "Article follow failed for %s %s at %s: %s",
                    entity_kind,
                    entity_label,
                    href,
                    article.error,
                )
                continue
            clear_failed_url(href)
            if not is_recent_article(article.published_at, max_age_days=article_max_age_days):
                logger.info("Skipping stale fetched article %s for %s %s", href, entity_kind, entity_label)
                continue

            title = article.title or link.get("text") or href
            article_key = normalized_href or href
            source_metadata = get_source_metadata(href)
            source = source_metadata["domain"]
            normalized_article_title = normalize_title(title)
            content_hash = build_content_hash(article.text)
            score_bundle = compute_article_scores(article, link, href, source_metadata)
            body = article.text
            published_at = article.published_at or None
            extracted_article_payload = {
                "url": article.url,
                "title": article.title,
                "text": article.text,
                "published_at": article.published_at,
                "success": article.success,
                "error": article.error,
                "reused_existing_article": False,
            }
            logger.info("Fetched new article %s for %s %s", href, entity_kind, entity_label)

        context = {
            "entity": entity,
            "entity_kind": entity_kind,
            "entity_label": entity_label,
            "source_page_url": source_page_url,
            "link": link,
            "href": href,
            "source": source,
            "article_key": article_key,
            "title": title,
            "body": body,
            "published_at": published_at,
            "normalized_href": normalized_href,
            "normalized_article_title": normalized_article_title,
            "content_hash": content_hash,
            "source_metadata": source_metadata,
            "score_bundle": score_bundle,
            "extracted_article_payload": extracted_article_payload,
            "existing_article_id": existing_article_id,
            "reused_existing_article": bool(existing_article_id),
        }

        save_article(
            {
                **context,
                "raw_json": build_raw_json(context),
            }
        )
        saved_count += 1
        logger.info(
            "Saved article %s for %s %s (%s/%s saved for this page)",
            href,
            entity_kind,
            entity_label,
            saved_count,
            max_articles,
        )

    return saved_count
