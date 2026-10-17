from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, TypedDict

from pipelines._article_follow import (
    NewsEntity,
    collect_article_urls_to_fetch,
    save_followed_article_links,
)
from core.CommonPipeline import MAX_ARTICLES_PER_SEARCH_PAGE
from engine.page_fetch import fetch_articles, fetch_source_pages
from pipelines.job_builder import group_jobs_by_url, unique_job_urls


class ArticleSaveRequest(TypedDict):
    source_page_url: str
    candidate_links: list[dict]
    entity: NewsEntity
    max_articles: int
    source_type: str


def _page_has_usable_links(page: dict[str, Any]) -> bool:
    links = page.get("links")
    return isinstance(links, list) and len(links) > 0


def direct_article_save_request(url: str, entity: NewsEntity, summary: str | None = None) -> ArticleSaveRequest:
    return {
        "source_page_url": url,
        "candidate_links": [{"href": url, "text": summary}],
        "entity": entity,
        "max_articles": 1,
        "source_type": "article",
    }


def build_search_article_save_requests(
    *,
    crawled_pages: list[dict],
    jobs_by_url: dict[str, list[dict]],
    logger,
    entity_from_job: Callable[[dict], NewsEntity],
    candidate_links_for: Callable[[dict, dict], list[dict]],
) -> list[ArticleSaveRequest]:
    # Assemble the search article save requests so downstream callers can work from one normalized shape.
    requests: list[ArticleSaveRequest] = []

    for page in crawled_pages:
        request_url = str(page.get("request_url") or "").strip()
        page_url = str(page.get("url") or "").strip()

        matching_jobs = []
        if request_url:
            matching_jobs = jobs_by_url.get(request_url, [])
        if not matching_jobs and page_url:
            matching_jobs = jobs_by_url.get(page_url, [])

        status = page.get("status")
        fetch_succeeded = status is not None and int(status) < 400
        if not page.get("success") and not (fetch_succeeded and _page_has_usable_links(page)):
            for job in matching_jobs:
                entity = entity_from_job(job)
                logger.warning(
                    "Source page crawl failed for %s %s at %s: %s",
                    entity.kind,
                    entity.label,
                    page.get("url"),
                    page.get("error"),
                )
            continue

        for job in matching_jobs:
            requests.append(
                {
                    "source_page_url": page["url"],
                    "candidate_links": candidate_links_for(page, job),
                    "entity": entity_from_job(job),
                    "max_articles": MAX_ARTICLES_PER_SEARCH_PAGE,
                    "source_type": job["source_type"],
                }
            )

    return requests


def collect_save_request_article_urls(
    save_requests: list[ArticleSaveRequest],
    *,
    should_include_link: Callable[[str, dict], bool] | None = None,
) -> list[str]:
    # Collect the save request article urls into one cleaned result for the next step.
    urls_to_fetch: list[str] = []
    seen_urls: set[str] = set()

    for request in save_requests:
        added_for_request = 0
        max_articles = int(request["max_articles"])
        for link in request["candidate_links"]:
            if added_for_request >= max_articles:
                break

            href = str(link.get("href") or "").strip()
            if not href:
                continue
            if href in seen_urls:
                added_for_request += 1
                continue

            batch_urls = collect_article_urls_to_fetch(
                [link],
                1,
                should_include_link=should_include_link,
            )
            if not batch_urls:
                continue

            urls_to_fetch.extend(batch_urls)
            seen_urls.update(batch_urls)
            added_for_request += 1

    return urls_to_fetch


def run_article_save_requests(
    *,
    save_requests: list[ArticleSaveRequest],
    logger,
    should_include_link: Callable[[str, dict], bool] | None = None,
    by_source_type: bool = False,
) -> dict:
    # Fetch every needed article in one batch, then save each request; counts are keyed by entity id (or (id, source_type)).
    saved_counts: dict = defaultdict(int)
    article_urls = collect_save_request_article_urls(
        save_requests,
        should_include_link=should_include_link,
    )
    fetched_articles = fetch_articles(article_urls) if article_urls else {}

    for request in save_requests:
        entity = request["entity"]
        key = (entity.id, request["source_type"]) if by_source_type else entity.id
        saved_counts[key] += save_followed_article_links(
            source_page_url=request["source_page_url"],
            candidate_links=request["candidate_links"],
            entity=entity,
            max_articles=request["max_articles"],
            fetched_articles=fetched_articles,
            logger=logger,
            should_include_link=should_include_link,
        )

    return saved_counts


def run_mixed_job_orchestration(
    *,
    jobs: list[dict],
    logger,
    entity_from_job: Callable[[dict], NewsEntity],
    candidate_links_for: Callable[[dict, dict], list[dict]],
    by_source_type: bool = False,
) -> dict:
    # Crawl search/listing pages for candidate links, add direct article jobs, then save everything.
    search_jobs = [job for job in jobs if job["source_type"] in {"search", "listing"}]
    crawled_pages = fetch_source_pages(unique_job_urls(search_jobs)) if search_jobs else []

    save_requests = build_search_article_save_requests(
        crawled_pages=crawled_pages,
        jobs_by_url=group_jobs_by_url(search_jobs),
        logger=logger,
        entity_from_job=entity_from_job,
        candidate_links_for=candidate_links_for,
    )
    save_requests.extend(
        direct_article_save_request(job["url"], entity_from_job(job))
        for job in jobs
        if job["source_type"] == "article"
    )
    return run_article_save_requests(
        save_requests=save_requests,
        logger=logger,
        by_source_type=by_source_type,
    )
