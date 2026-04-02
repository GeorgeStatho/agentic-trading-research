from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:
    WEBSCRAPING_DIR = Path(__file__).resolve().parents[1]
    if str(WEBSCRAPING_DIR) not in sys.path:
        sys.path.append(str(WEBSCRAPING_DIR))

from pipelines._shared import (
    MAX_ARTICLE_AGE_DAYS,
    fetch_existing_article_by_url,
    get_log_file_path,
    get_scrape_logger,
)
from pipelines._entity_adapters import (
    make_direct_request_builder,
    make_entity_article_saver,
    make_request_saver,
)
from pipelines._orchestration import run_article_save_requests
from pipelines._sector_rss import (
    DEFAULT_RSS_MAX_AGE_DAYS,
    extract_recent_rss_urls,
    get_recent_sector_feed_article_urls,
)
from pipelines.job_builder import SectorSourceJob, build_sector_rss_jobs

from db_helpers import add_sector_news_article, get_all_sectors, initialize_news_database


LOGGER = get_scrape_logger("sector_pipeline")
__all__ = [
    "DEFAULT_RSS_MAX_AGE_DAYS",
    "extract_recent_rss_urls",
    "get_all_sector_news",
    "get_sector_news",
    "get_sector_news_from_rss",
]


def _is_cnbc_url(url: str) -> bool:
    return "cnbc.com" in (url or "").lower()


def _find_sector(sector_identifier: str) -> dict | None:
    needle = " ".join(sector_identifier.split()).strip().lower()
    if not needle:
        return None

    sectors = get_all_sectors()

    for sector in sectors:
        if str(sector.get("sector_key") or "").strip().lower() == needle:
            return sector

    for sector in sectors:
        if str(sector.get("name") or "").strip().lower() == needle:
            return sector

    return None


_save_followed_article_links = make_entity_article_saver(
    logger=LOGGER,
    entity_kind="sector",
    entity_label=lambda sector: sector["name"],
    save_article=lambda *, entity, source_page_url, context, **extra: add_sector_news_article(
        sector_id=entity["id"],
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
        "sector_id": entity["id"],
        "sector_key": entity["sector_key"],
        "sector_name": entity["name"],
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


_build_sector_direct_article_save_requests = make_direct_request_builder(
    entity_from_job=lambda job: job["sector"],
    text_from_job=lambda job: job["sector"]["name"],
    build_request_payload=lambda job: {
        "sector": job["sector"],
    },
    max_articles=1,
)


_save_sector_request = make_request_saver(
    save_followed_links=_save_followed_article_links,
    entity_from_payload=lambda payload: payload["sector"],
)


def _process_sector_jobs(jobs: list[SectorSourceJob]) -> dict[int, int]:
    save_requests = _build_sector_direct_article_save_requests(jobs)
    return run_article_save_requests(
        save_requests=save_requests,
        save_request=_save_sector_request,
        should_include_link=lambda href, link: _is_cnbc_url(href),
    )


def _classify_sector_article_urls(urls: list[str]) -> tuple[list[str], list[str]]:
    existing_urls: list[str] = []
    uncrawled_urls: list[str] = []

    for url in urls:
        if fetch_existing_article_by_url(url) is not None:
            existing_urls.append(url)
        else:
            uncrawled_urls.append(url)

    return existing_urls, uncrawled_urls


def _build_sector_jobs(sector: dict, urls: list[str]) -> list[SectorSourceJob]:
    existing_urls, uncrawled_urls = _classify_sector_article_urls(urls)
    LOGGER.info(
        "Sector %s (%s) RSS URLs: %s existing article(s), %s uncrawled article(s)",
        sector["name"],
        sector["sector_key"],
        len(existing_urls),
        len(uncrawled_urls),
    )
    # Keep both sets as jobs so existing articles can still be linked to the
    # sector, while the shared article-follow path skips re-fetching them.
    return build_sector_rss_jobs(sector, [*existing_urls, *uncrawled_urls])


def _build_sector_jobs_from_rss(sector: dict, max_age_days: int = DEFAULT_RSS_MAX_AGE_DAYS) -> list[SectorSourceJob]:
    article_urls = get_recent_sector_feed_article_urls(sector, max_age_days=max_age_days)
    LOGGER.info(
        "Collected %s unique RSS article URLs for sector %s (%s)",
        len(article_urls),
        sector["name"],
        sector["sector_key"],
    )
    return _build_sector_jobs(sector, article_urls)


def _build_all_sector_jobs_from_rss(sectors: list[dict], max_age_days: int = DEFAULT_RSS_MAX_AGE_DAYS) -> list[SectorSourceJob]:
    jobs: list[SectorSourceJob] = []

    for sector in sectors:
        LOGGER.info(
            "Fetching RSS feeds for sector %s (%s)",
            sector["name"],
            sector["sector_key"],
        )
        jobs.extend(_build_sector_jobs_from_rss(sector, max_age_days=max_age_days))

    return jobs


def get_sector_news(sector_identifier: str, urls: list[str], source_page_url: str = "https://www.cnbc.com/") -> int:
    initialize_news_database()
    sector = _find_sector(sector_identifier)
    if sector is None:
        raise ValueError(f"Sector not found for identifier: {sector_identifier}")

    jobs = _build_sector_jobs(sector, urls)
    LOGGER.info("Starting sector scrape for %s (%s) from %s URLs", sector["name"], sector["sector_key"], len(jobs))
    saved_counts = _process_sector_jobs(jobs)
    saved = saved_counts.get(sector["id"], 0)
    LOGGER.info(
        "Finished sector scrape for %s (%s): saved %s articles. Log file: %s",
        sector["name"],
        sector["sector_key"],
        saved,
        get_log_file_path(),
    )
    print(f"Saved {saved} articles for sector {sector['name']} ({sector['sector_key']})")
    print(f"Scrape log written to {get_log_file_path()}")
    return saved


def get_all_sector_news(max_age_days: int = DEFAULT_RSS_MAX_AGE_DAYS) -> None:
    initialize_news_database()
    sectors = get_all_sectors()
    LOGGER.info("Starting all-sector scrape for %s sectors", len(sectors))
    jobs = _build_all_sector_jobs_from_rss(sectors, max_age_days=max_age_days)
    saved_counts = _process_sector_jobs(jobs)

    for sector in sectors:
        saved = saved_counts.get(sector["id"], 0)
        print(f"Saved {saved} articles for sector {sector['name']} ({sector['sector_key']})")

    LOGGER.info("Finished all-sector scrape. Log file: %s", get_log_file_path())
    print(f"Scrape log written to {get_log_file_path()}")


def get_sector_news_from_rss(sector_identifier: str, max_age_days: int = DEFAULT_RSS_MAX_AGE_DAYS) -> int:
    initialize_news_database()
    sector = _find_sector(sector_identifier)
    if sector is None:
        raise ValueError(f"Sector not found for identifier: {sector_identifier}")

    jobs = _build_sector_jobs_from_rss(sector, max_age_days=max_age_days)
    article_urls = [job["url"] for job in jobs]
    return get_sector_news(sector_identifier, article_urls)

if __name__ == "__main__":
    try:
        LOGGER.info("Running sector news pipeline directly")
        #get_sector_news_from_rss("Technology")
        get_all_sector_news()
    except KeyboardInterrupt:
        LOGGER.warning("Sector scrape interrupted by user. Log file: %s", get_log_file_path())
        print(f"\nScrape interrupted by user. Log file: {get_log_file_path()}")
