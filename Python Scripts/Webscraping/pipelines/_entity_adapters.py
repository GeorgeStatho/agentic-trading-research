from __future__ import annotations

from typing import Any, Callable

from pipelines._article_follow import save_followed_article_links
from pipelines._orchestration import (
    ArticleSaveRequest,
    build_direct_article_save_requests,
    build_search_article_save_requests,
)
from pipelines._shared import MAX_ARTICLE_AGE_DAYS


def make_entity_article_saver(
    *,
    logger,
    entity_kind: str,
    entity_label: Callable[[dict], str],
    save_article: Callable[..., Any],
    build_raw_json: Callable[..., dict[str, Any]],
):
    def _save(
        *,
        source_page_url: str,
        candidate_links: list[dict],
        entity: dict,
        max_articles: int,
        max_age_days: int = MAX_ARTICLE_AGE_DAYS,
        fetched_articles: dict | None = None,
        should_include_link: Callable[[str, dict], bool] | None = None,
        **extra: Any,
    ) -> int:
        return save_followed_article_links(
            source_page_url=source_page_url,
            candidate_links=candidate_links,
            entity=entity,
            entity_kind=entity_kind,
            entity_label=entity_label(entity),
            logger=logger,
            max_articles=max_articles,
            max_age_days=max_age_days,
            fetched_articles=fetched_articles,
            should_include_link=should_include_link,
            save_article=lambda context: save_article(
                entity=entity,
                source_page_url=source_page_url,
                context=context,
                **extra,
            ),
            build_raw_json=lambda context: build_raw_json(
                entity=entity,
                source_page_url=source_page_url,
                context=context,
                **extra,
            ),
        )

    return _save


def make_search_request_builder(
    *,
    logger,
    should_process_page: Callable[[dict], bool],
    build_candidate_links: Callable[[dict, dict], list[dict]],
    build_request_payload: Callable[[dict], dict[str, Any]],
    entity_from_job: Callable[[dict], dict],
    max_articles: int,
    failure_message: Callable[[dict, dict], tuple[str, tuple[Any, ...]]] | None = None,
):
    def _build(crawled_pages: list[dict], jobs_by_url: dict[str, list[dict]]) -> list[ArticleSaveRequest]:
        def _handle_failure(page: dict, jobs: list[dict]) -> None:
            if failure_message is None:
                return
            for job in jobs:
                message, args = failure_message(page, job)
                logger.warning(message, *args)

        return build_search_article_save_requests(
            crawled_pages=crawled_pages,
            jobs_by_url=jobs_by_url,
            logger=logger,
            should_process_page=should_process_page,
            handle_page_failure=_handle_failure if failure_message is not None else None,
            build_candidate_links=build_candidate_links,
            build_request=lambda page, job, candidate_links: {
                "source_page_url": page["url"],
                "candidate_links": candidate_links,
                "entity": entity_from_job(job),
                "entity_id": entity_from_job(job)["id"],
                "max_articles": max_articles,
                "payload": build_request_payload(job),
            },
        )

    return _build


def make_direct_request_builder(
    *,
    entity_from_job: Callable[[dict], dict],
    text_from_job: Callable[[dict], str],
    build_request_payload: Callable[[dict], dict[str, Any]],
    max_articles: int = 1,
):
    return lambda jobs: build_direct_article_save_requests(
        jobs=jobs,
        build_request=lambda job: {
            "source_page_url": job["url"],
            "candidate_links": [
                {
                    "href": job["url"],
                    "text": text_from_job(job),
                }
            ],
            "entity": entity_from_job(job),
            "entity_id": entity_from_job(job)["id"],
            "max_articles": max_articles,
            "payload": build_request_payload(job),
        },
    )


def make_request_saver(
    *,
    save_followed_links,
    entity_from_payload: Callable[[dict[str, Any]], dict],
    extra_from_payload: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
):
    def _save(request: ArticleSaveRequest, fetched_articles: dict) -> int:
        payload = request["payload"]
        extra = extra_from_payload(payload) if extra_from_payload is not None else {}
        return save_followed_links(
            source_page_url=request["source_page_url"],
            candidate_links=request["candidate_links"],
            entity=entity_from_payload(payload),
            max_articles=request["max_articles"],
            fetched_articles=fetched_articles,
            **extra,
        )

    return _save


def make_bucketed_count_accumulator(
    *,
    bucket_from_request: Callable[[ArticleSaveRequest], str],
    initial_buckets: dict[str, int],
):
    def _accumulate(saved_counts: dict, request: ArticleSaveRequest, saved: int) -> None:
        entity_id = request["entity_id"]
        bucket = bucket_from_request(request)
        if entity_id not in saved_counts:
            saved_counts[entity_id] = dict(initial_buckets)
        saved_counts[entity_id][bucket] += saved

    return _accumulate
