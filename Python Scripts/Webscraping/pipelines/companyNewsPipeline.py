from __future__ import annotations

from contextlib import closing
from functools import partial
from typing import Any, Callable

from core.scrape_logging import get_log_file_path, get_scrape_logger
from sources.source_config import supports_source_type
from pipelines._article_follow import NewsEntity
from pipelines._internal import link_match_texts, normalize_match_text
from pipelines._orchestration import (
    run_mixed_job_orchestration,
)
from pipelines.job_builder import (
    CompanySourceJob,
    build_company_source_jobs,
    build_yahoo_news_jobs,
)
from APIs.alpacaNewsAPI import get_stock_data as get_alpaca_news_stock_data
from APIs.foolAPI import get_stock_data as get_fool_stock_data
from APIs.gurufocusAPI import get_stock_data as get_gurufocus_stock_data
from market_data.yFinanceNews import extract_title_url_publisher, get_company_news_items
from APIs.morningStarAPI import get_stock_data as get_morningstar_stock_data

from market_db_modules.queries import get_all_companies, get_all_industries
from news_db_modules.bootstrap import initialize_database as initialize_news_database
from news_db_modules.links import link_company_to_article
from market_data.hydration import ensure_all_sector_market_data, ensure_industry_market_data


LOGGER = get_scrape_logger("company_pipeline")
API_ARTICLE_LIMIT = 10
API_SOURCES: tuple[tuple[str, Any], ...] = (
    ("fool_api", get_fool_stock_data),
    ("morningstar_api", get_morningstar_stock_data),
    ("gurufocus_api", get_gurufocus_stock_data),
    ("alpaca_news_api", get_alpaca_news_stock_data),
)
__all__ = [
    "get_all_company_news",
    "get_company_news",
    "get_industries_company_news",
    "get_top_companies_news",
    "get_top_processed_industries_company_news",
]

def _extract_api_news_pairs(payload: Any) -> list[tuple[str, str]]:
    # Extract the api news pairs from the raw response and return a stable value.
    pairs: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    records = payload if isinstance(payload, list) else [payload]
    for record in records:
        if not isinstance(record, dict):
            continue
        data = record.get("data")
        if not isinstance(data, dict):
            continue

        for field_name in ("articles", "analysis", "transcripts"):
            items = data.get(field_name)
            if not isinstance(items, list):
                continue

            for item in items:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                title = " ".join(str(item.get("title") or "").split()).strip()
                if not url or not title or url in seen_urls:
                    continue
                pairs.append((title, url))
                seen_urls.add(url)

    return pairs


def _build_api_jobs(company: dict) -> list[CompanySourceJob]:
    # Assemble the api jobs so downstream callers can work from one normalized shape.
    symbol = str(company.get("symbol") or "").strip()
    if not symbol:
        return []

    jobs: list[CompanySourceJob] = []

    for source_name, fetch_stock_data in API_SOURCES:
        try:
            payload = fetch_stock_data(symbol, limit=API_ARTICLE_LIMIT)
        except Exception as exc:
            LOGGER.warning("%s lookup failed for %s: %s", source_name, symbol, exc)
            continue

        news_pairs = _extract_api_news_pairs(payload)
        if not news_pairs:
            continue

        jobs.extend(
            build_yahoo_news_jobs(
                company,
                news_pairs,
                source_name=source_name,
                source_type="article",
            )
        )

    return jobs


CORPORATE_SUFFIXES = frozenset(
    "inc incorporated corp corporation co company plc ltd limited lp llc sa nv se holdings holding group".split()
)
MIN_REUSE_VARIANT_LENGTH = 3


def _company_reuse_filter(company: dict) -> Callable[[dict], bool]:
    # An already-saved article found on a search page is reused only if the company is named in the link.
    name_tokens = normalize_match_text(company.get("name")).split()
    trimmed_tokens = list(name_tokens)
    while trimmed_tokens and trimmed_tokens[-1] in CORPORATE_SUFFIXES:
        trimmed_tokens.pop()
    variants = {
        f" {variant} "
        for variant in (" ".join(name_tokens), " ".join(trimmed_tokens), normalize_match_text(company.get("symbol")))
        if len(variant) >= MIN_REUSE_VARIANT_LENGTH
    }

    def may_reuse(link: dict) -> bool:
        return any(variant in f" {text} " for text in link_match_texts(link) for variant in variants)

    return may_reuse


def _company_entity(company: dict, search_term: str, publisher: str = "") -> NewsEntity:
    return NewsEntity(
        kind="company",
        id=company["id"],
        label=company["symbol"],
        identity={
            "company_id": company["id"],
            "symbol": company["symbol"],
            "company_name": company["name"],
            "industry_key": company["industry_key"],
            "industry_name": company["industry_name"],
            "sector_key": company["sector_key"],
            "sector_name": company["sector_name"],
            "search_term": search_term,
            **({"provenance": {"publisher": publisher}} if publisher else {}),
        },
        link=partial(link_company_to_article, company["id"]),
    )


def _company_candidate_links(page: dict, job: dict) -> list[dict]:
    if not supports_source_type(page["url"], "search"):
        return []
    return list(page.get("links") or [])


def _build_company_jobs(company: dict) -> list[CompanySourceJob]:
    jobs = build_company_source_jobs([company])
    jobs.extend(_build_api_jobs(company))

    symbol = str(company.get("symbol") or "").strip()
    if not symbol:
        return jobs

    try:
        yahoo_news_items = get_company_news_items(symbol)
    except Exception as exc:
        LOGGER.warning("Yahoo Finance news fetch failed for %s: %s", symbol, exc)
        return jobs

    jobs.extend(build_yahoo_news_jobs(company, extract_title_url_publisher(yahoo_news_items)))
    return jobs


def _build_all_company_jobs(companies: list[dict]) -> list[CompanySourceJob]:
    jobs: list[CompanySourceJob] = []

    for company in companies:
        jobs.extend(_build_company_jobs(company))

    return jobs


def _process_company_jobs(jobs: list[CompanySourceJob]) -> dict[int, int]:
    return run_mixed_job_orchestration(
        jobs=jobs,
        logger=LOGGER,
        entity_from_job=lambda job: _company_entity(job["company"], job["search_term"], job.get("publisher", "")),
        candidate_links_for=_company_candidate_links,
        reuse_filter_for=lambda job: _company_reuse_filter(job["company"]),
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

    ensure_industry_market_data(industry_identifier)
    industries = get_all_industries()

    for industry in industries:
        if str(industry.get("industry_key") or "").strip().lower() == needle:
            return industry

    for industry in industries:
        if str(industry.get("name") or "").strip().lower() == needle:
            return industry

    return None


def _get_companies_for_industries(industry_identifiers: list[str]) -> tuple[list[dict], list[dict]]:
    # Load the companies for industries and normalize it for downstream processing.
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


def _get_top_companies(limit: int) -> list[dict]:
    normalized_limit = max(0, int(limit))
    if normalized_limit <= 0:
        return []

    def _company_sort_key(company: dict) -> tuple[float, str]:
        market_weight = company.get("market_weight")
        try:
            normalized_weight = float(market_weight)
        except (TypeError, ValueError):
            normalized_weight = float("-inf")
        return (normalized_weight, str(company.get("symbol") or ""))

    companies = get_all_companies()
    ranked_companies = sorted(
        companies,
        key=_company_sort_key,
        reverse=True,
    )
    return ranked_companies[:normalized_limit]


def _get_top_processed_sector_keys(limit: int) -> list[str]:
    # Load the top processed sector keys and normalize it for downstream processing.
    normalized_limit = max(0, int(limit))
    if normalized_limit <= 0:
        return []

    from db_common import get_connection

    with closing(get_connection()) as conn, conn:
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
    # Load the top processed industry keys for sector and normalize it for downstream processing.
    normalized_limit = max(0, int(limit))
    normalized_sector_key = str(sector_key or "").strip()
    if normalized_limit <= 0 or not normalized_sector_key:
        return []

    from db_common import get_connection

    with closing(get_connection()) as conn, conn:
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
    # Load the company news and normalize it for downstream processing.
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
    ensure_all_sector_market_data()
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
    # Load the industries company news and normalize it for downstream processing.
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


def get_top_companies_news(*, top_company_count: int = 25) -> int:
    # Load the top companies news and normalize it for downstream processing.
    initialize_news_database()
    ensure_all_sector_market_data()
    companies = _get_top_companies(top_company_count)
    if not companies:
        LOGGER.info("No top companies available for company news scrape")
        print("No top companies available")
        return 0

    LOGGER.info(
        "Starting company scrape for top %s companies by market weight",
        len(companies),
    )
    jobs = _build_all_company_jobs(companies)
    saved_counts = _process_company_jobs(jobs)

    total_saved = 0
    for company in companies:
        saved = saved_counts.get(company["id"], 0)
        total_saved += saved
        print(f"Saved {saved} articles for {company['name']} ({company['symbol']})")

    LOGGER.info(
        "Finished company scrape for top %s companies: saved %s articles. Log file: %s",
        len(companies),
        total_saved,
        get_log_file_path(),
    )
    print(f"Scrape log written to {get_log_file_path()}")
    return total_saved


def get_top_processed_industries_company_news(
    *,
    top_sector_count: int = 10,
    top_industry_count: int = 10,
) -> int:
    # Load the top processed industries company news and normalize it for downstream processing.
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
        get_company_news("AAPL")
        #get_all_company_news()
        #get_top_processed_industries_company_news()
    except KeyboardInterrupt:
        LOGGER.warning("Company scrape interrupted by user. Log file: %s", get_log_file_path())
        print(f"\nScrape interrupted by user. Log file: {get_log_file_path()}")
