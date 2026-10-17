from __future__ import annotations

from typing import Callable

from engine.page_fetch import fetch_feed
from pipelines._article_follow import NewsEntity
from core.scrape_logging import get_log_file_path, get_scrape_logger
from pipelines._orchestration import direct_article_save_request, run_article_save_requests
from pipelines._sector_rss import DEFAULT_RSS_MAX_AGE_DAYS, extract_recent_rss_items


LOGGER = get_scrape_logger("region_pipeline")


def _is_cnbc_url(url: str) -> bool:
    return "cnbc.com" in (url or "").lower()


def make_region_pipeline(
    *,
    logger_name: str,
    region_slug: str,
    region_label: str,
    rss_feed_url: str,
    link: Callable[..., int],
):
    # Build the full region-news pipeline function so callers can reuse one configured orchestration flow.
    logger = get_scrape_logger(logger_name)
    entity = NewsEntity(
        kind=region_slug,
        id=1,
        label=region_label,
        identity={"region_slug": region_slug, "region_name": region_label, "rss_feed_url": rss_feed_url},
        link=link,
    )

    def _save_items(items: list[tuple[str, str | None]]) -> int:
        save_requests = [direct_article_save_request(url, entity, summary=description) for url, description in items]
        logger.info("Starting %s scrape from %s URLs", region_label, len(save_requests))
        saved = run_article_save_requests(
            save_requests=save_requests,
            logger=logger,
            should_include_link=lambda href, _link: _is_cnbc_url(href),
        ).get(entity.id, 0)
        logger.info(
            "Finished %s scrape: saved %s articles. Log file: %s",
            region_label,
            saved,
            get_log_file_path(),
        )
        print(f"Saved {saved} {region_label} articles")
        print(f"Scrape log written to {get_log_file_path()}")
        return saved

    def get_news(urls: list[str]) -> int:
        return _save_items([(url, None) for url in urls if str(url).strip()])

    def get_news_from_rss(max_age_days: int = DEFAULT_RSS_MAX_AGE_DAYS) -> int:
        return _save_items(extract_recent_rss_items(fetch_feed(rss_feed_url), max_age_days=max_age_days))

    return {
        "get_news": get_news,
        "get_news_from_rss": get_news_from_rss,
    }
