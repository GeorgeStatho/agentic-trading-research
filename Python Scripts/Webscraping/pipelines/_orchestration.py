from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, TypedDict

from pipelines._article_follow import collect_article_urls_to_fetch
from pipelines._shared import crawl_article_pages, crawl_articles


class ArticleSaveRequest(TypedDict):
    source_page_url: str
    candidate_links: list[dict]
    entity: dict
    entity_id: int
    max_articles: int
    payload: dict[str, Any]


def _page_has_usable_links(page: dict[str, Any]) -> bool:
    links = page.get("links")
    return isinstance(links, list) and len(links) > 0


def build_search_article_save_requests(
    *,
    crawled_pages: list[dict],
    jobs_by_url: dict[str, list[dict]],
    logger,
    should_process_page: Callable[[dict], bool],
    build_candidate_links: Callable[[dict, dict], list[dict]],
    build_request: Callable[[dict, dict, list[dict]], ArticleSaveRequest],
    handle_page_failure: Callable[[dict, list[dict]], None] | None = None,
) -> list[ArticleSaveRequest]:
    requests: list[ArticleSaveRequest] = []

    for page in crawled_pages:
        matching_jobs = jobs_by_url.get(page["url"], [])
        status = page.get("status")
        fetch_succeeded = status is not None and int(status) < 400
        if not page.get("success") and not (fetch_succeeded and _page_has_usable_links(page)):
            if handle_page_failure is not None:
                handle_page_failure(page, matching_jobs)
            continue
        if not should_process_page(page):
            continue

        for job in matching_jobs:
            candidate_links = build_candidate_links(page, job)
            requests.append(build_request(page, job, candidate_links))

    return requests


def build_direct_article_save_requests(
    *,
    jobs: list[dict],
    build_request: Callable[[dict], ArticleSaveRequest],
) -> list[ArticleSaveRequest]:
    return [build_request(job) for job in jobs]


def collect_save_request_article_urls(
    save_requests: list[ArticleSaveRequest],
    *,
    should_include_link: Callable[[str, dict], bool] | None = None,
) -> list[str]:
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
    save_request: Callable[[ArticleSaveRequest, dict], int],
    should_include_link: Callable[[str, dict], bool] | None = None,
    accumulate_saved_count: Callable[[dict, ArticleSaveRequest, int], None] | None = None,
) -> dict:
    saved_counts: dict = defaultdict(int)
    article_urls = collect_save_request_article_urls(
        save_requests,
        should_include_link=should_include_link,
    )
    fetched_articles = crawl_article_pages(article_urls) if article_urls else {}

    for request in save_requests:
        saved = save_request(request, fetched_articles)
        if accumulate_saved_count is not None:
            accumulate_saved_count(saved_counts, request, saved)
        else:
            saved_counts[request["entity_id"]] += saved

    return saved_counts


def run_mixed_job_orchestration(
    *,
    search_jobs: list[dict],
    direct_article_jobs: list[dict],
    group_jobs_by_url: Callable[[list[dict]], dict[str, list[dict]]],
    unique_job_urls: Callable[[list[dict]], list[str]],
    build_search_save_requests: Callable[[list[dict], dict[str, list[dict]]], list[ArticleSaveRequest]],
    build_direct_article_save_requests: Callable[[list[dict]], list[ArticleSaveRequest]],
    save_request: Callable[[ArticleSaveRequest, dict], int],
    should_include_link: Callable[[str, dict], bool] | None = None,
    accumulate_saved_count: Callable[[dict, ArticleSaveRequest, int], None] | None = None,
) -> dict:
    crawled_pages: list[dict] = []

    if search_jobs:
        jobs_by_url = group_jobs_by_url(search_jobs)
        urls = unique_job_urls(search_jobs)
        crawled_pages = crawl_articles(urls)
    else:
        jobs_by_url = {}

    save_requests = build_search_save_requests(crawled_pages, jobs_by_url)
    save_requests.extend(build_direct_article_save_requests(direct_article_jobs))
    return run_article_save_requests(
        save_requests=save_requests,
        save_request=save_request,
        should_include_link=should_include_link,
        accumulate_saved_count=accumulate_saved_count,
    )
