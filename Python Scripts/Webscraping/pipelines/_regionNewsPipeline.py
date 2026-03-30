from __future__ import annotations

from typing import Callable

from pipelines._shared import fetch_existing_article_by_url, get_log_file_path, get_scrape_logger
from pipelines._entity_adapters import make_direct_request_builder, make_entity_article_saver, make_request_saver
from pipelines._orchestration import run_article_save_requests
from pipelines._sector_rss import DEFAULT_RSS_MAX_AGE_DAYS, extract_recent_rss_urls, fetch_rss_feed_xml


LOGGER = get_scrape_logger("region_pipeline")


def _is_cnbc_url(url: str) -> bool:
    return "cnbc.com" in (url or "").lower()


def _classify_article_urls(urls: list[str]) -> tuple[list[str], list[str]]:
    existing_urls: list[str] = []
    uncrawled_urls: list[str] = []

    for url in urls:
        if fetch_existing_article_by_url(url) is not None:
            existing_urls.append(url)
        else:
            uncrawled_urls.append(url)

    return existing_urls, uncrawled_urls


def make_region_pipeline(
    *,
    logger_name: str,
    region_slug: str,
    region_label: str,
    rss_feed_url: str,
    save_article: Callable[..., int],
):
    logger = get_scrape_logger(logger_name)
    region_context = {
        "id": 1,
        "slug": region_slug,
        "name": region_label,
        "rss_feed_url": rss_feed_url,
    }

    save_followed_article_links = make_entity_article_saver(
        logger=logger,
        entity_kind=region_slug,
        entity_label=lambda region: region["name"],
        save_article=lambda *, entity, source_page_url, context, **extra: save_article(
            source=context["source"],
            article_key=context["article_key"],
            title=context["title"],
            source_url=context["href"],
            source_page_url=source_page_url,
            summary=context["link"].get("text"),
            body=context["body"],
            published_at=context["published_at"],
            age_days=context["score_bundle"].get("age_days"),
            recency_score=context["score_bundle"].get("recency_score"),
            source_reputation_score=context["score_bundle"].get("source_reputation_score"),
            directness_score=context["score_bundle"].get("directness_score"),
            confirmation_score=context["score_bundle"].get("confirmation_score"),
            independent_source_count=context["score_bundle"].get("independent_source_count"),
            factuality_score=context["score_bundle"].get("factuality_score"),
            evidence_score=context["score_bundle"].get("evidence_score"),
            raw_json=context["raw_json"],
        ),
        build_raw_json=lambda *, entity, source_page_url, context, **extra: {
            "region_slug": entity["slug"],
            "region_name": entity["name"],
            "rss_feed_url": entity["rss_feed_url"],
            "source_page_url": source_page_url,
            "link": context["link"],
            "normalized_url": context["normalized_href"],
            "normalized_title": context["normalized_article_title"],
            "content_hash": context["content_hash"],
            "source_metadata": context["source_metadata"],
            "scores": context["score_bundle"],
            "extracted_article": context["extracted_article_payload"],
        },
    )

    build_direct_article_save_requests = make_direct_request_builder(
        entity_from_job=lambda job: job["region"],
        text_from_job=lambda job: job["region"]["name"],
        build_request_payload=lambda job: {
            "region": job["region"],
        },
        max_articles=1,
    )

    save_request = make_request_saver(
        save_followed_links=save_followed_article_links,
        entity_from_payload=lambda payload: payload["region"],
    )

    def _build_jobs(urls: list[str]) -> list[dict]:
        existing_urls, uncrawled_urls = _classify_article_urls(urls)
        logger.info(
            "%s RSS URLs: %s existing article(s), %s uncrawled article(s)",
            region_label,
            len(existing_urls),
            len(uncrawled_urls),
        )
        deduped_urls = [*existing_urls, *uncrawled_urls]
        return [
            {
                "url": url,
                "region": region_context,
                "source_name": "cnbc_rss",
                "source_type": "article",
            }
            for url in deduped_urls
            if str(url).strip()
        ]

    def _process_jobs(jobs: list[dict]) -> dict[int, int]:
        save_requests = build_direct_article_save_requests(jobs)
        return run_article_save_requests(
            save_requests=save_requests,
            save_request=save_request,
            should_include_link=lambda href, link: _is_cnbc_url(href),
        )

    def get_news(urls: list[str]) -> int:
        jobs = _build_jobs(urls)
        logger.info("Starting %s scrape from %s URLs", region_label, len(jobs))
        saved_counts = _process_jobs(jobs)
        saved = saved_counts.get(region_context["id"], 0)
        logger.info(
            "Finished %s scrape: saved %s articles. Log file: %s",
            region_label,
            saved,
            get_log_file_path(),
        )
        print(f"Saved {saved} {region_label} articles")
        print(f"Scrape log written to {get_log_file_path()}")
        return saved

    def get_news_from_rss(max_age_days: int = DEFAULT_RSS_MAX_AGE_DAYS) -> int:
        rss_xml = fetch_rss_feed_xml(rss_feed_url)
        urls = extract_recent_rss_urls(rss_xml, max_age_days=max_age_days)
        logger.info(
            "Collected %s unique RSS article URLs for %s from %s",
            len(urls),
            region_label,
            rss_feed_url,
        )
        return get_news(urls)

    return {
        "get_news": get_news,
        "get_news_from_rss": get_news_from_rss,
        "context": region_context,
    }

