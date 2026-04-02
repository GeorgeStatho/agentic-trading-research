from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:
    WEBSCRAPING_DIR = Path(__file__).resolve().parents[1]
    if str(WEBSCRAPING_DIR) not in sys.path:
        sys.path.append(str(WEBSCRAPING_DIR))

AGENT_CALLERS_DIR = Path(__file__).resolve().parents[2] / "agentCallers"
if str(AGENT_CALLERS_DIR) not in sys.path:
    sys.path.append(str(AGENT_CALLERS_DIR))

from pipelines._shared import (
    MAX_ARTICLES_PER_SEARCH_PAGE,
    MAX_ARTICLE_AGE_DAYS,
    filter_article_links,
    get_log_file_path,
    get_scrape_logger,
    supports_source_type,
)
from pipelines._constants import COMPANY_NAME_SUFFIXES
from pipelines._entity_adapters import (
    make_direct_request_builder,
    make_entity_article_saver,
    make_request_saver,
    make_search_request_builder,
)
from pipelines._internal import is_blacklisted_cnbc_link, link_matches_variants, normalize_match_text
from pipelines._orchestration import (
    run_mixed_job_orchestration,
)
from pipelines.job_builder import (
    CompanySourceJob,
    build_company_source_jobs,
    build_yahoo_news_jobs,
    group_jobs_by_url,
    unique_job_urls,
)
from market_data.yFinanceNews import extract_title_and_url, get_company_news_items

from db_helpers import add_company_news_article, get_all_companies, get_all_industries, initialize_news_database


LOGGER = get_scrape_logger("company_pipeline")
__all__ = [
    "get_all_company_news",
    "get_company_news",
    "get_industries_company_news",
    "get_top_processed_industries_company_news",
]

def _build_company_match_variants(company: dict) -> set[str]:
    # Build a few deterministic match variants so we can require that search
    # results actually mention the target company before following them.
    company_name = normalize_match_text(company.get("name"))
    if not company_name:
        return set()

    variants: set[str] = {company_name}
    tokens = company_name.split()

    trimmed_tokens = tokens[:]
    while trimmed_tokens and trimmed_tokens[-1] in COMPANY_NAME_SUFFIXES:
        trimmed_tokens.pop()
        if trimmed_tokens:
            variants.add(" ".join(trimmed_tokens))

    symbol = normalize_match_text(company.get("symbol"))
    if len(symbol) >= 3:
        variants.add(symbol)

    return {variant for variant in variants if len(variant) >= 3}


def _filter_company_candidate_links(page_url: str, links: list[dict], company: dict) -> list[dict]:
    # Start from the shared source/article-pattern filter, then apply
    # company-specific relevance checks to remove generic market pages.
    base_candidates = filter_article_links(page_url, links)
    if not base_candidates:
        return []

    variants = _build_company_match_variants(company)
    if not variants:
        return []

    filtered: list[dict] = []
    for link in base_candidates:
        href = str(link.get("href") or "")
        if not href:
            continue
        if is_blacklisted_cnbc_link(href):
            LOGGER.info("Skipping blacklisted CNBC URL %s for company %s", href, company["symbol"])
            continue

        if link_matches_variants(link, variants):
            filtered.append(link)
            continue

        LOGGER.info(
            "Skipping weak company match URL %s for company %s because the link text did not match the company name",
            href,
            company["symbol"],
        )

    return filtered


_save_followed_article_links = make_entity_article_saver(
    logger=LOGGER,
    entity_kind="company",
    entity_label=lambda company: company["symbol"],
    save_article=lambda *, entity, source_page_url, context, **extra: add_company_news_article(
        company_id=entity["id"],
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
    build_raw_json=lambda *, entity, source_page_url, context, search_term: {
        "company_id": entity["id"],
        "symbol": entity["symbol"],
        "company_name": entity["name"],
        "industry_key": entity["industry_key"],
        "industry_name": entity["industry_name"],
        "sector_key": entity["sector_key"],
        "sector_name": entity["sector_name"],
        "search_term": search_term,
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


def _process_source_page(page: dict, company: dict, search_term: str) -> int:
    if not page.get("success"):
        LOGGER.warning(
            "Source page crawl failed for company %s at %s: %s",
            company["symbol"],
            page.get("url"),
            page.get("error"),
        )
        return 0
    if not supports_source_type(page["url"], "search"):
        return 0

    # Source pages are shallow discovery pages; after filtering their links we
    # hand off to the article follower to fetch the actual article bodies.
    candidate_links = _filter_company_candidate_links(page["url"], page["links"], company)
    return _save_followed_article_links(page["url"], candidate_links, company, search_term)


_build_company_search_save_requests = make_search_request_builder(
    logger=LOGGER,
    should_process_page=lambda page: supports_source_type(page["url"], "search"),
    build_candidate_links=lambda page, job: _filter_company_candidate_links(
        page["url"],
        page["links"],
        job["company"],
    ),
    build_request_payload=lambda job: {
        "company": job["company"],
        "search_term": job["search_term"],
    },
    entity_from_job=lambda job: job["company"],
    max_articles=MAX_ARTICLES_PER_SEARCH_PAGE,
    failure_message=lambda page, job: (
        "Source page crawl failed for company %s at %s: %s",
        (job["company"]["symbol"], page.get("url"), page.get("error")),
    ),
)


_build_company_direct_article_save_requests = make_direct_request_builder(
    entity_from_job=lambda job: job["company"],
    text_from_job=lambda job: job["search_term"],
    build_request_payload=lambda job: {
        "company": job["company"],
        "search_term": job["search_term"],
    },
)


_save_company_request = make_request_saver(
    save_followed_links=_save_followed_article_links,
    entity_from_payload=lambda payload: payload["company"],
    extra_from_payload=lambda payload: {
        "search_term": payload["search_term"],
    },
)


def _build_company_jobs(company: dict) -> list[CompanySourceJob]:
    jobs = build_company_source_jobs([company])

    symbol = str(company.get("symbol") or "").strip()
    if not symbol:
        return jobs

    try:
        yahoo_news_items = get_company_news_items(symbol)
    except Exception as exc:
        LOGGER.warning("Yahoo Finance news fetch failed for %s: %s", symbol, exc)
        return jobs

    yahoo_news_pairs = extract_title_and_url(yahoo_news_items)
    jobs.extend(build_yahoo_news_jobs(company, yahoo_news_pairs))
    return jobs


def _build_all_company_jobs(companies: list[dict]) -> list[CompanySourceJob]:
    jobs: list[CompanySourceJob] = []

    for company in companies:
        jobs.extend(_build_company_jobs(company))

    return jobs


def _process_company_jobs(jobs: list[CompanySourceJob]) -> dict[int, int]:
    search_jobs = [job for job in jobs if job["source_type"] == "search"]
    direct_article_jobs = [job for job in jobs if job["source_type"] == "article"]
    return run_mixed_job_orchestration(
        search_jobs=search_jobs,
        direct_article_jobs=direct_article_jobs,
        group_jobs_by_url=group_jobs_by_url,
        unique_job_urls=unique_job_urls,
        build_search_save_requests=_build_company_search_save_requests,
        build_direct_article_save_requests=_build_company_direct_article_save_requests,
        save_request=_save_company_request,
    )


def _find_company(company_identifier: str) -> dict | None:
    # Resolve a user-facing identifier to a DB company record by trying the
    # ticker first, then falling back to an exact company-name match.
    needle = " ".join(company_identifier.split()).strip().lower()
    if not needle:
        return None

    companies = get_all_companies()

    for company in companies:
        if str(company.get("symbol") or "").strip().lower() == needle:
            return company

    for company in companies:
        if str(company.get("name") or "").strip().lower() == needle:
            return company

    return None


def _find_industry(industry_identifier: str) -> dict | None:
    needle = " ".join(str(industry_identifier or "").split()).strip().lower()
    if not needle:
        return None

    industries = get_all_industries()

    for industry in industries:
        if str(industry.get("industry_key") or "").strip().lower() == needle:
            return industry

    for industry in industries:
        if str(industry.get("name") or "").strip().lower() == needle:
            return industry

    return None


def _get_companies_for_industries(industry_identifiers: list[str]) -> tuple[list[dict], list[dict]]:
    resolved_industries: list[dict] = []
    seen_industry_ids: set[int] = set()

    for industry_identifier in industry_identifiers:
        industry = _find_industry(industry_identifier)
        if industry is None:
            raise ValueError(f"Industry not found for identifier: {industry_identifier}")

        industry_id = int(industry["id"])
        if industry_id in seen_industry_ids:
            continue
        seen_industry_ids.add(industry_id)
        resolved_industries.append(industry)

    allowed_industry_ids = {int(industry["id"]) for industry in resolved_industries}
    companies = [
        company
        for company in get_all_companies()
        if int(company["industry_id"]) in allowed_industry_ids
    ]
    return resolved_industries, companies


def _get_top_processed_sector_keys(limit: int) -> list[str]:
    normalized_limit = max(0, int(limit))
    if normalized_limit <= 0:
        return []

    from db_helpers import DB_PATH, get_connection

    with get_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT
                combined.sector_key,
                COUNT(*) AS high_confidence_count
            FROM (
                SELECT
                    s.sector_key,
                    wsi.confidence
                FROM world_news_sector_impacts AS wsi
                JOIN sectors AS s ON s.id = wsi.sector_id

                UNION ALL

                SELECT
                    s.sector_key,
                    usi.confidence
                FROM us_news_sector_impacts AS usi
                JOIN sectors AS s ON s.id = usi.sector_id
            ) AS combined
            WHERE lower(coalesce(combined.confidence, '')) = 'high'
            GROUP BY combined.sector_key
            ORDER BY high_confidence_count DESC, combined.sector_key ASC
            LIMIT ?
            """,
            (normalized_limit,),
        ).fetchall()

    return [str(row["sector_key"]) for row in rows if str(row["sector_key"] or "").strip()]


def _get_top_processed_industry_keys_for_sector(sector_key: str, limit: int) -> list[str]:
    normalized_limit = max(0, int(limit))
    normalized_sector_key = str(sector_key or "").strip()
    if normalized_limit <= 0 or not normalized_sector_key:
        return []

    from db_helpers import DB_PATH, get_connection

    with get_connection(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT
                i.industry_key,
                COUNT(*) AS high_confidence_count
            FROM industry_opportunist_impacts AS ioi
            JOIN industries AS i ON i.id = ioi.industry_id
            JOIN sectors AS s ON s.id = i.sector_id
            WHERE s.sector_key = ?
              AND lower(coalesce(ioi.confidence, '')) = 'high'
            GROUP BY i.industry_key
            ORDER BY high_confidence_count DESC, i.industry_key ASC
            LIMIT ?
            """,
            (normalized_sector_key, normalized_limit),
        ).fetchall()

    return [str(row["industry_key"]) for row in rows if str(row["industry_key"] or "").strip()]


def get_company_news(company_identifier: str) -> int:
    initialize_news_database()
    company = _find_company(company_identifier)
    if company is None:
        raise ValueError(f"Company not found for identifier: {company_identifier}")

    LOGGER.info("Starting single-company scrape for %s (%s)", company["name"], company["symbol"])
    # For single-company runs we still use the same bulk-crawl flow so the
    # behavior matches the full pipeline and logs stay comparable.
    jobs = _build_company_jobs(company)
    saved_counts = _process_company_jobs(jobs)
    saved = saved_counts.get(company["id"], 0)
    LOGGER.info(
        "Finished single-company scrape for %s (%s): saved %s articles. Log file: %s",
        company["name"],
        company["symbol"],
        saved,
        get_log_file_path(),
    )
    print(f"Saved {saved} articles for {company['name']} ({company['symbol']})")
    print(f"Scrape log written to {get_log_file_path()}")
    return saved


def get_all_company_news() -> None:
    initialize_news_database()
    companies = get_all_companies()
    # Run the shared discovery/follow/save process for every company using one
    # crawl batch, then report how many articles were saved per company.
    LOGGER.info("Starting all-company scrape for %s companies", len(companies))
    jobs = _build_all_company_jobs(companies)
    saved_counts = _process_company_jobs(jobs)

    for company in companies:
        saved = saved_counts.get(company["id"], 0)
        print(f"Saved {saved} articles for {company['name']} ({company['symbol']})")
    LOGGER.info("Finished all-company scrape. Log file: %s", get_log_file_path())
    print(f"Scrape log written to {get_log_file_path()}")


def get_industries_company_news(industry_identifiers: list[str]) -> int:
    initialize_news_database()
    industries, companies = _get_companies_for_industries(industry_identifiers)
    if not companies:
        LOGGER.info("No companies found for industries: %s", industry_identifiers)
        print("No companies found for the supplied industries")
        return 0

    LOGGER.info(
        "Starting company scrape for %s companies across %s industries",
        len(companies),
        len(industries),
    )
    jobs = _build_all_company_jobs(companies)
    saved_counts = _process_company_jobs(jobs)

    total_saved = 0
    for company in companies:
        saved = saved_counts.get(company["id"], 0)
        total_saved += saved
        print(f"Saved {saved} articles for {company['name']} ({company['symbol']})")

    LOGGER.info(
        "Finished company scrape for industries %s: saved %s articles. Log file: %s",
        [industry["industry_key"] for industry in industries],
        total_saved,
        get_log_file_path(),
    )
    print(f"Scrape log written to {get_log_file_path()}")
    return total_saved


def get_top_processed_industries_company_news(
    *,
    top_sector_count: int = 3,
    top_industry_count: int = 3,
) -> int:
    sector_keys = _get_top_processed_sector_keys(top_sector_count)
    if not sector_keys:
        LOGGER.info("No processed sectors available for top-industry company news scrape")
        print("No processed sectors available")
        return 0

    selected_industry_keys: list[str] = []
    seen_industry_keys: set[str] = set()
    for sector_key in sector_keys:
        top_industry_keys = _get_top_processed_industry_keys_for_sector(sector_key, top_industry_count)
        for industry_key in top_industry_keys:
            normalized_key = str(industry_key or "").strip()
            if not normalized_key or normalized_key in seen_industry_keys:
                continue
            seen_industry_keys.add(normalized_key)
            selected_industry_keys.append(normalized_key)

    if not selected_industry_keys:
        LOGGER.info(
            "No processed industries available for sectors %s during company news scrape",
            sector_keys,
        )
        print("No processed industries available from the top sectors")
        return 0

    LOGGER.info(
        "Starting company scrape for top processed industries %s derived from sectors %s",
        selected_industry_keys,
        sector_keys,
    )
    return get_industries_company_news(selected_industry_keys)


if __name__ == "__main__":
    try:
        #get_company_news("Apple Inc.")
        #get_all_company_news()
        get_top_processed_industries_company_news()
    except KeyboardInterrupt:
        LOGGER.warning("Company scrape interrupted by user. Log file: %s", get_log_file_path())
        print(f"\nScrape interrupted by user. Log file: {get_log_file_path()}")
