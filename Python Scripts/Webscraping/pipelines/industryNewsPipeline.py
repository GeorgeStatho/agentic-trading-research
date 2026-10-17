from __future__ import annotations

from functools import partial

from pipelines._shared import (
    MAX_ARTICLE_AGE_DAYS,
    filter_article_links,
    get_log_file_path,
    get_scrape_logger,
    supports_source_type,
)
from engine.listing_page_helper import extract_listing_article_links
from pipelines._constants import INDUSTRY_NAME_STOPWORDS
from pipelines._article_follow import NewsEntity
from pipelines._internal import is_blacklisted_cnbc_link, link_matches_variants, normalize_match_text
from pipelines._orchestration import (
    run_mixed_job_orchestration,
)
from pipelines.job_builder import build_industry_source_jobs

from news_db_modules.links import link_industry_to_article
from market_db_modules.queries import get_all_industries, get_all_sectors
from news_db_modules.bootstrap import initialize_database as initialize_news_database
from market_data.hydration import (
    ensure_all_sector_market_data,
    ensure_industry_market_data,
    ensure_sector_market_data,
)


LOGGER = get_scrape_logger("industry_pipeline")
__all__ = [
    "get_all_industry_news",
    "get_industry_news",
    "get_sector_industry_news",
    "list_supported_industries",
]


def _expand_industry_variant_forms(value: str) -> set[str]:
    # Handle the expand industry variant forms flow in one place so callers can rely on a single, well-defined result.
    variants = {value}
    tokens = value.split()

    def _token_forms(token: str) -> set[str]:
        forms = {token}
        if len(token) > 4 and token.endswith("ies"):
            forms.add(token[:-3] + "y")
        if len(token) > 4 and token.endswith("es"):
            forms.add(token[:-2])
        if len(token) > 4 and token.endswith("s"):
            forms.add(token[:-1])
        return {form for form in forms if len(form) >= 4}

    if tokens:
        token_options = [_token_forms(token) for token in tokens]
        expanded_tokens = [sorted(options) for options in token_options]

        def _walk(index: int, current: list[str]) -> None:
            if index >= len(expanded_tokens):
                variants.add(" ".join(current))
                return
            for option in expanded_tokens[index]:
                _walk(index + 1, [*current, option])

        _walk(0, [])

    return {variant for variant in variants if len(variant) >= 4}


def _build_industry_match_variants(industry: dict) -> set[str]:
    # Assemble the industry match variants so downstream callers can work from one normalized shape.
    industry_name = normalize_match_text(industry.get("name"))
    if not industry_name:
        return set()

    variants: set[str] = {industry_name}
    tokens = [token for token in industry_name.split() if token not in INDUSTRY_NAME_STOPWORDS]

    if tokens:
        variants.add(" ".join(tokens))

    if len(tokens) >= 2:
        variants.update(tokens)
        variants.update({" ".join(tokens[:2]), " ".join(tokens[-2:])})

    industry_key = normalize_match_text(industry.get("industry_key"))
    if industry_key:
        variants.add(industry_key)
        key_tokens = [token for token in industry_key.split() if token not in INDUSTRY_NAME_STOPWORDS]
        if key_tokens:
            variants.add(" ".join(key_tokens))
            variants.update(key_tokens)

    expanded_variants: set[str] = set()
    for variant in variants:
        expanded_variants.update(_expand_industry_variant_forms(variant))

    return {variant for variant in expanded_variants if len(variant) >= 4}


def _filter_industry_candidate_links(page_url: str, links: list[dict], industry: dict) -> list[dict]:
    # Filter the industry candidate links down to the rows this helper should keep.
    base_candidates = filter_article_links(page_url, links)
    if not base_candidates:
        return []

    variants = _build_industry_match_variants(industry)
    if not variants:
        return []

    filtered: list[dict] = []
    for link in base_candidates:
        href = str(link.get("href") or "")
        if not href:
            continue
        if is_blacklisted_cnbc_link(href):
            LOGGER.info("Skipping blacklisted CNBC URL %s for industry %s", href, industry["name"])
            continue

        if link_matches_variants(link, variants):
            filtered.append(link)
            continue

        LOGGER.info(
            "Skipping weak industry match URL %s for industry %s because the link text did not match the industry name",
            href,
            industry["name"],
        )

    return filtered


def _industry_entity(industry: dict) -> NewsEntity:
    return NewsEntity(
        kind="industry",
        id=industry["id"],
        label=industry["name"],
        identity={
            "industry_id": industry["id"],
            "industry_key": industry["industry_key"],
            "industry_name": industry["name"],
        },
        link=partial(link_industry_to_article, industry["id"]),
    )


def _industry_candidate_links(page: dict, job: dict) -> list[dict]:
    if not supports_source_type(page["url"], job["source_type"]):
        return []
    if job["source_type"] == "listing":
        return extract_listing_article_links(page["url"], page["links"], job["industry"]["name"])
    return _filter_industry_candidate_links(page["url"], page["links"], job["industry"])


def _process_industry_jobs(jobs: list[dict]) -> dict:
    return run_mixed_job_orchestration(
        jobs=jobs,
        logger=LOGGER,
        entity_from_job=lambda job: _industry_entity(job["industry"]),
        candidate_links_for=_industry_candidate_links,
        by_source_type=True,
    )


def _find_industry(industry_identifier: str) -> dict | None:
    needle = " ".join(industry_identifier.split()).strip().lower()
    if not needle:
        return None

    ensure_industry_market_data(industry_identifier)
    industries = get_all_industries()

    for industry in industries:
        if str(industry.get("industry_key") or "").strip().lower() == needle:
            return industry

    for industry in industries:
        if str(industry.get("name") or "").strip().lower() == needle:
            return industry

    return None


def _find_sector(sector_identifier: str) -> dict | None:
    needle = " ".join(str(sector_identifier or "").split()).strip().lower()
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


def _get_industries_for_sector(sector_identifier: str) -> tuple[dict, list[dict]]:
    ensure_sector_market_data(sector_identifier)
    sector = _find_sector(sector_identifier)
    if sector is None:
        raise ValueError(f"Sector not found for identifier: {sector_identifier}")

    sector_key = str(sector.get("sector_key") or "").strip().lower()
    industries = [
        industry
        for industry in get_all_industries()
        if str(industry.get("sector_key") or "").strip().lower() == sector_key
    ]
    return sector, industries


def list_supported_industries() -> list[dict[str, str]]:
    industries = get_all_industries()
    return [
        {
            "industry_key": str(industry.get("industry_key") or ""),
            "name": str(industry.get("name") or ""),
        }
        for industry in industries
    ]


def get_industry_news(industry_identifier: str) -> int:
    # Load the industry news and normalize it for downstream processing.
    initialize_news_database()
    industry = _find_industry(industry_identifier)
    if industry is None:
        raise ValueError(f"Industry not found for identifier: {industry_identifier}")

    LOGGER.info(
        "Starting single-industry scrape for %s (%s)",
        industry["name"],
        industry["industry_key"],
    )
    jobs = build_industry_source_jobs([industry])
    saved_counts = _process_industry_jobs(jobs)

    listing_saved = saved_counts.get((industry["id"], "listing"), 0)
    search_saved = saved_counts.get((industry["id"], "search"), 0)
    total_saved = listing_saved + search_saved
    LOGGER.info(
        "Finished single-industry scrape for %s (%s): saved %s articles. Log file: %s",
        industry["name"],
        industry["industry_key"],
        total_saved,
        get_log_file_path(),
    )
    print(f"Saved {listing_saved} listing-page articles for {industry['name']}")
    print(f"Saved {search_saved} search-page articles for {industry['name']}")
    print(f"Scrape log written to {get_log_file_path()}")
    return total_saved


def get_sector_industry_news(sector_identifier: str) -> int:
    # Load the sector industry news and normalize it for downstream processing.
    initialize_news_database()
    sector, industries = _get_industries_for_sector(sector_identifier)
    if not industries:
        LOGGER.info(
            "No industries found for sector %s (%s)",
            sector["name"],
            sector["sector_key"],
        )
        print(f"No industries found for sector {sector['name']}")
        return 0

    LOGGER.info(
        "Starting sector-scoped industry scrape for %s (%s) across %s industries",
        sector["name"],
        sector["sector_key"],
        len(industries),
    )
    jobs = build_industry_source_jobs(industries)
    saved_counts = _process_industry_jobs(jobs)

    total_saved = 0
    for industry in industries:
        listing_saved = saved_counts.get((industry["id"], "listing"), 0)
        search_saved = saved_counts.get((industry["id"], "search"), 0)
        industry_total = listing_saved + search_saved
        total_saved += industry_total
        print(f"Saved {listing_saved} listing-page articles for {industry['name']}")
        print(f"Saved {search_saved} search-page articles for {industry['name']}")

    LOGGER.info(
        "Finished sector-scoped industry scrape for %s (%s): saved %s articles. Log file: %s",
        sector["name"],
        sector["sector_key"],
        total_saved,
        get_log_file_path(),
    )
    print(f"Scrape log written to {get_log_file_path()}")
    return total_saved


def get_all_industry_news() -> None:
    initialize_news_database()
    ensure_all_sector_market_data()
    industries = get_all_industries()
    # Crawl all industry source pages in a single batch, then process listing
    # pages first and search pages second from the normalized crawl results.
    LOGGER.info("Starting all-industry scrape for %s industries", len(industries))
    jobs = build_industry_source_jobs(industries)
    saved_counts = _process_industry_jobs(jobs)

    for industry in industries:
        listing_saved = saved_counts.get((industry["id"], "listing"), 0)
        print(f"Saved {listing_saved} listing-page articles for {industry['name']}")

        search_saved = saved_counts.get((industry["id"], "search"), 0)
        print(f"Saved {search_saved} search-page articles for {industry['name']}")
    LOGGER.info("Finished all-industry scrape. Log file: %s", get_log_file_path())
    print(f"Scrape log written to {get_log_file_path()}")


if __name__ == "__main__":
    try:
        get_sector_industry_news("financial-services")
        #get_all_industry_news()
        #get_industry_news("semiconductors")
    except KeyboardInterrupt:
        LOGGER.warning("Industry scrape interrupted by user. Log file: %s", get_log_file_path())
        print(f"\nScrape interrupted by user. Log file: {get_log_file_path()}")
