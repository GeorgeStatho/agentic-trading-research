from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from typing import Any, Callable, cast

from core.CommonPipeline import (
    clear_failed_url,
    compute_article_scores,
    is_recent_article,
    record_failed_url,
)
from engine.article_extraction import ArticleExtractionResult
from processing.news_normalization import build_content_hash, normalize_title, normalize_url
from pipelines._link_policy import LinkVerdict
from sources.source_config import get_source_metadata
from db_common import get_connection
from news_db_modules.article_store import add_news_article

@dataclass(frozen=True)
class NewsEntity:
    kind: str  # "company" | "industry" | "sector" | "us" | "world"
    id: int
    label: str
    identity: dict
    link: Callable[..., int]  # called as link(article_id=, source_page_url=, conn=)


def _persist(entity: NewsEntity, source_page_url: str, article_id: int | None = None, **article: Any) -> int:
    # Insert the article unless it already exists, then link it to the entity in the same transaction.
    with closing(get_connection()) as conn, conn:
        if article_id is None:
            article_id = add_news_article(**article, conn=conn)
        entity.link(article_id=article_id, source_page_url=source_page_url, conn=conn)
    return article_id


LOGGED_SKIP_REASONS = ("blacklisted", "failed_url", "stale_existing", "stale_url_date", "irrelevant_existing")


def _insert_fetched_article(
    *, verdict: LinkVerdict, article: ArticleExtractionResult, source_page_url: str, entity: NewsEntity, logger
) -> int | None:
    # Score and insert one freshly fetched article; None means it was rejected (failed or stale).
    href = verdict.href
    if not article.success:
        record_failed_url(href, "article_follow", article.error)
        logger.warning(
            "Article follow failed for %s %s at %s: %s", entity.kind, entity.label, href, article.error
        )
        return None
    clear_failed_url(href)
    if not is_recent_article(article.published_at, max_age_days=verdict.max_age_days):
        logger.info("Skipping stale fetched article %s for %s %s", href, entity.kind, entity.label)
        return None

    link = verdict.link
    normalized_href = verdict.normalized_url
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
    article_id = _persist(
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
    return article_id


def save_link_verdicts(
    *,
    source_page_url: str,
    verdicts: list[LinkVerdict],
    entity: NewsEntity,
    fetched_articles: dict[str, ArticleExtractionResult],
    logger,
    outcomes: dict[str, int | None],
) -> int:
    # Link reused articles and insert new ones for one entity. `outcomes` is shared by every request in a run
    # (normalized_url -> article id, or None if rejected) so a url is fetched, inserted and recorded only once.
    normalized_fetched_articles: dict[str, ArticleExtractionResult] = {}
    for fetched_url, fetched_article in fetched_articles.items():
        normalized_fetched_url = normalize_url(fetched_url)
        if normalized_fetched_url and normalized_fetched_url not in normalized_fetched_articles:
            normalized_fetched_articles[normalized_fetched_url] = fetched_article

    logger.info(
        "Processing %s candidate article links for %s %s from %s",
        len(verdicts),
        entity.kind,
        entity.label,
        source_page_url,
    )

    saved_count = 0
    limit_skips = 0
    for verdict in verdicts:
        href = verdict.href
        if verdict.action == "skip":
            if verdict.reason == "limit":
                limit_skips += 1
            elif verdict.reason in LOGGED_SKIP_REASONS:
                logger.info("Skipping %s article %s for %s %s", verdict.reason, href, entity.kind, entity.label)
            continue

        if verdict.action == "reuse":
            _persist(entity, source_page_url, article_id=verdict.article_id)
            logger.info("Reusing existing article %s for %s %s", href, entity.kind, entity.label)
        elif verdict.normalized_url in outcomes:
            article_id = outcomes[verdict.normalized_url]
            if article_id is None:
                continue
            _persist(entity, source_page_url, article_id=article_id)
            logger.info("Linked article %s fetched earlier in this run for %s %s", href, entity.kind, entity.label)
        else:
            article = cast(
                ArticleExtractionResult | None,
                fetched_articles.get(href) or normalized_fetched_articles.get(verdict.normalized_url),
            )
            if article is None:
                logger.warning(
                    "Missing crawled article result for %s %s at %s", entity.kind, entity.label, href
                )
                continue
            article_id = _insert_fetched_article(
                verdict=verdict, article=article, source_page_url=source_page_url, entity=entity, logger=logger
            )
            outcomes[verdict.normalized_url] = article_id
            if article_id is None:
                continue

        saved_count += 1
        logger.info("Saved article %s for %s %s (%s saved for this page)", href, entity.kind, entity.label, saved_count)

    if limit_skips:
        logger.info(
            "Skipped %s article links over the fetch limit for %s %s from %s",
            limit_skips,
            entity.kind,
            entity.label,
            source_page_url,
        )
    return saved_count
