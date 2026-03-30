from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:
    WEBSCRAPING_DIR = Path(__file__).resolve().parents[1]
    if str(WEBSCRAPING_DIR) not in sys.path:
        sys.path.append(str(WEBSCRAPING_DIR))

from pipelines._shared import (
    MAX_ARTICLE_AGE_DAYS,
    get_log_file_path,
    get_scrape_logger,
)
from pipelines._entity_adapters import (
    make_direct_request_builder,
    make_entity_article_saver,
    make_request_saver,
)
from pipelines._orchestration import run_article_save_requests

from db_helpers import add_sector_news_article, get_all_sectors, initialize_news_database


LOGGER = get_scrape_logger("sector_pipeline")
__all__ = ["get_sector_news"]


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


def get_sector_news(sector_identifier: str, urls: list[str], source_page_url: str = "https://www.cnbc.com/") -> int:
    initialize_news_database()
    sector = _find_sector(sector_identifier)
    if sector is None:
        raise ValueError(f"Sector not found for identifier: {sector_identifier}")

    jobs = [
        {
            "url": str(url).strip(),
            "sector": sector,
        }
        for url in urls
        if str(url).strip()
    ]
    LOGGER.info("Starting sector scrape for %s (%s) from %s URLs", sector["name"], sector["sector_key"], len(jobs))
    save_requests = _build_sector_direct_article_save_requests(jobs)
    saved_counts = run_article_save_requests(
        save_requests=save_requests,
        save_request=_save_sector_request,
        should_include_link=lambda href, link: _is_cnbc_url(href),
    )
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
