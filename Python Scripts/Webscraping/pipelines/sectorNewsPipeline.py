from __future__ import annotations

from functools import partial

from pipelines._article_follow import NewsEntity
from core.scrape_logging import get_log_file_path, get_scrape_logger
from pipelines._orchestration import ArticleSaveRequest, direct_article_save_request, run_article_save_requests
from pipelines._sector_rss import (
    DEFAULT_RSS_MAX_AGE_DAYS,
    get_recent_sector_feed_items,
)
from pipelines.job_builder import SectorSourceJob, build_sector_rss_jobs

from news_db_modules.links import link_sector_to_article
from market_db_modules.queries import get_all_sectors
from news_db_modules.bootstrap import initialize_database as initialize_news_database


LOGGER = get_scrape_logger("sector_pipeline")
__all__ = [
    "DEFAULT_RSS_MAX_AGE_DAYS",
    "get_all_sector_news",
    "get_sector_news",
    "get_sector_news_from_rss",
]


def _is_cnbc_url(url: str) -> bool:
    return "newswire.example.com" in (url or "").lower()


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


def _sector_entity(sector: dict) -> NewsEntity:
    return NewsEntity(
        kind="sector",
        id=sector["id"],
        label=sector["name"],
        identity={
            "sector_id": sector["id"],
            "sector_key": sector["sector_key"],
            "sector_name": sector["name"],
        },
        link=partial(link_sector_to_article, sector["id"]),
    )


def _sector_save_requests(sector: dict, jobs: list[SectorSourceJob]) -> list[ArticleSaveRequest]:
    entity = _sector_entity(sector)
    return [direct_article_save_request(job["url"], entity, summary=job.get("summary")) for job in jobs]


def _save_sector_requests(save_requests: list[ArticleSaveRequest]) -> dict[int, int]:
    return run_article_save_requests(
        save_requests=save_requests,
        logger=LOGGER,
        should_include_link=lambda href, link: _is_cnbc_url(href),
    )


def _run_sector_jobs(sector: dict, jobs: list[SectorSourceJob]) -> int:
    LOGGER.info("Starting sector scrape for %s (%s) from %s URLs", sector["name"], sector["sector_key"], len(jobs))
    saved = _save_sector_requests(_sector_save_requests(sector, jobs)).get(sector["id"], 0)
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


def _get_sector_rss_jobs(sector: dict, max_age_days: int) -> list[SectorSourceJob]:
    items = get_recent_sector_feed_items(sector, max_age_days=max_age_days)
    LOGGER.info(
        "Collected %s unique RSS article URLs for sector %s (%s)",
        len(items),
        sector["name"],
        sector["sector_key"],
    )
    return build_sector_rss_jobs(sector, items)


def _require_sector(sector_identifier: str) -> dict:
    sector = _find_sector(sector_identifier)
    if sector is None:
        raise ValueError(f"Sector not found for identifier: {sector_identifier}")
    return sector


def get_sector_news(sector_identifier: str, urls: list[str], source_page_url: str = "https://newswire.example.com/") -> int:
    initialize_news_database()
    sector = _require_sector(sector_identifier)
    return _run_sector_jobs(sector, build_sector_rss_jobs(sector, [(url, None) for url in urls]))


def get_all_sector_news(max_age_days: int = DEFAULT_RSS_MAX_AGE_DAYS) -> None:
    initialize_news_database()
    sectors = get_all_sectors()
    LOGGER.info("Starting all-sector scrape for %s sectors", len(sectors))
    save_requests: list[ArticleSaveRequest] = []

    for sector in sectors:
        LOGGER.info(
            "Fetching RSS feeds for sector %s (%s)",
            sector["name"],
            sector["sector_key"],
        )
        save_requests.extend(_sector_save_requests(sector, _get_sector_rss_jobs(sector, max_age_days)))

    saved_counts = _save_sector_requests(save_requests)

    for sector in sectors:
        saved = saved_counts.get(sector["id"], 0)
        print(f"Saved {saved} articles for sector {sector['name']} ({sector['sector_key']})")

    LOGGER.info("Finished all-sector scrape. Log file: %s", get_log_file_path())
    print(f"Scrape log written to {get_log_file_path()}")


def get_sector_news_from_rss(sector_identifier: str, max_age_days: int = DEFAULT_RSS_MAX_AGE_DAYS) -> int:
    initialize_news_database()
    sector = _require_sector(sector_identifier)
    return _run_sector_jobs(sector, _get_sector_rss_jobs(sector, max_age_days))


if __name__ == "__main__":
    try:
        LOGGER.info("Running sector news pipeline directly")
        #get_sector_news_from_rss("Technology")
        get_all_sector_news()
    except KeyboardInterrupt:
        LOGGER.warning("Sector scrape interrupted by user. Log file: %s", get_log_file_path())
        print(f"\nScrape interrupted by user. Log file: {get_log_file_path()}")
