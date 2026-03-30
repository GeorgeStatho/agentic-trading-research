from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:
    WEBSCRAPING_DIR = Path(__file__).resolve().parents[1]
    if str(WEBSCRAPING_DIR) not in sys.path:
        sys.path.append(str(WEBSCRAPING_DIR))

from pipelines._shared import (
    ArticleExtractionResult,
    MAX_ARTICLES_PER_SEARCH_PAGE,
    MAX_ARTICLE_AGE_DAYS,
    build_content_hash,
    clear_failed_url,
    compute_article_scores,
    crawl_article_pages,
    crawl_articles,
    fetch_existing_article_by_url,
    filter_article_links,
    get_log_file_path,
    get_max_article_age_days,
    get_scrape_logger,
    get_source_metadata,
    is_allowed_source,
    is_recent_article,
    normalize_title,
    normalize_url,
    record_failed_url,
    supports_source_type,
    cast,
)
from pipelines._constants import COMPANY_NAME_SUFFIXES
from pipelines._internal import is_blacklisted_cnbc_link, link_matches_variants, normalize_match_text
from pipelines.job_builder import (
    CompanySourceJob,
    build_company_source_jobs,
    build_yahoo_news_jobs,
    group_jobs_by_url,
    unique_job_urls,
)
from market_data.yFinanceNews import extract_title_and_url, get_company_news_items

from db_helpers import add_company_news_article, get_all_companies, initialize_news_database


LOGGER = get_scrape_logger("company_pipeline")
__all__ = ["get_all_company_news", "get_company_news"]

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


def _save_followed_article_links(
    source_page_url: str,
    candidate_links: list[dict],
    company: dict,
    search_term: str,
    max_articles: int = MAX_ARTICLES_PER_SEARCH_PAGE,
    max_age_days: int = MAX_ARTICLE_AGE_DAYS,
    fetched_articles: dict[str, ArticleExtractionResult] | None = None,
) -> int:
    saved_count = 0
    fetched_articles = dict(fetched_articles or {})
    # Follow the filtered result links one level deep, reuse any article that
    # already exists in the DB, and save new articles that pass recency checks.
    LOGGER.info(
        "Processing %s candidate article links for company %s from %s",
        len(candidate_links),
        company["symbol"],
        source_page_url,
    )

    urls_to_fetch: list[str] = []
    for link in candidate_links:
        if len(urls_to_fetch) >= max_articles:
            break

        href = link.get("href")
        if not href or fetch_existing_article_by_url(href) is not None:
            continue
        if not is_allowed_source(href):
            continue
        urls_to_fetch.append(href)

    if urls_to_fetch and not fetched_articles:
        LOGGER.info(
            "Fetching %s article pages through Scrapy for company %s",
            len(urls_to_fetch),
            company["symbol"],
        )
        fetched_articles = crawl_article_pages(urls_to_fetch)

    for link in candidate_links:
        if saved_count >= max_articles:
            LOGGER.info(
                "Reached article limit of %s for company %s from %s",
                max_articles,
                company["symbol"],
                source_page_url,
            )
            break

        href = link.get("href")
        if not href:
            continue
        if not is_allowed_source(href):
            LOGGER.info("Skipping disallowed article URL %s for company %s", href, company["symbol"])
            continue

        article_max_age_days = get_max_article_age_days(href, max_age_days)
        normalized_href = normalize_url(href)
        existing_article = fetch_existing_article_by_url(href)

        if existing_article is not None:
            existing_published_at = existing_article.get("published_at")
            if existing_published_at is not None and not isinstance(existing_published_at, str):
                existing_published_at = str(existing_published_at)
            if not is_recent_article(existing_published_at, max_age_days=article_max_age_days):
                LOGGER.info("Skipping stale existing article %s for company %s", href, company["symbol"])
                continue

            title = existing_article.get("title") or link.get("text") or href
            article_key = existing_article.get("article_key") or normalized_href or href
            source_metadata = get_source_metadata(existing_article.get("source_url") or href)
            source = existing_article.get("source") or source_metadata["domain"]
            normalized_article_title = existing_article.get("normalized_title") or normalize_title(title)
            content_hash = existing_article.get("content_hash") or build_content_hash(existing_article.get("body") or "")
            score_bundle = {
                "age_days": existing_article.get("age_days"),
                "recency_score": existing_article.get("recency_score"),
                "source_reputation_score": existing_article.get("source_reputation_score"),
                "directness_score": existing_article.get("directness_score"),
                "confirmation_score": existing_article.get("confirmation_score"),
                "independent_source_count": existing_article.get("independent_source_count"),
                "factuality_score": existing_article.get("factuality_score"),
                "evidence_score": existing_article.get("evidence_score"),
            }
            body = existing_article.get("body")
            published_at = existing_published_at
            extracted_article_payload = {
                "url": existing_article.get("source_url") or href,
                "title": existing_article.get("title"),
                "text": existing_article.get("body"),
                "published_at": existing_published_at,
                "success": True,
                "error": "",
                "reused_existing_article": True,
            }
            LOGGER.info("Reusing existing article %s for company %s", href, company["symbol"])
        else:
            article = cast(ArticleExtractionResult | None, fetched_articles.get(href))
            if article is None:
                LOGGER.warning(
                    "Missing crawled article result for company %s at %s",
                    company["symbol"],
                    href,
                )
                continue
            if not article.success:
                record_failed_url(href, "article_follow", article.error)
                LOGGER.warning(
                    "Article follow failed for company %s at %s: %s",
                    company["symbol"],
                    href,
                    article.error,
                )
                continue
            clear_failed_url(href)
            if not is_recent_article(article.published_at, max_age_days=article_max_age_days):
                LOGGER.info("Skipping stale fetched article %s for company %s", href, company["symbol"])
                continue

            title = article.title or link.get("text") or href
            article_key = normalized_href or href
            source_metadata = get_source_metadata(href)
            source = source_metadata["domain"]
            normalized_article_title = normalize_title(title)
            content_hash = build_content_hash(article.text)
            score_bundle = compute_article_scores(article, link, href, source_metadata)
            body = article.text
            published_at = article.published_at or None
            extracted_article_payload = {
                "url": article.url,
                "title": article.title,
                "text": article.text,
                "published_at": article.published_at,
                "success": article.success,
                "error": article.error,
                "reused_existing_article": False,
            }
            LOGGER.info("Fetched new article %s for company %s", href, company["symbol"])

        add_company_news_article(
            company_id=company["id"],
            source=source,
            article_key=article_key,
            title=title,
            source_url=href,
            source_page_url=source_page_url,
            summary=link.get("text"),
            body=body,
            published_at=published_at,
            age_days=score_bundle.get("age_days"),
            recency_score=score_bundle.get("recency_score"),
            source_reputation_score=score_bundle.get("source_reputation_score"),
            directness_score=score_bundle.get("directness_score"),
            confirmation_score=score_bundle.get("confirmation_score"),
            independent_source_count=score_bundle.get("independent_source_count"),
            factuality_score=score_bundle.get("factuality_score"),
            evidence_score=score_bundle.get("evidence_score"),
            raw_json={
                "company_id": company["id"],
                "symbol": company["symbol"],
                "company_name": company["name"],
                "industry_key": company["industry_key"],
                "industry_name": company["industry_name"],
                "sector_key": company["sector_key"],
                "sector_name": company["sector_name"],
                "search_term": search_term,
                "source_page_url": source_page_url,
                "link": link,
                "normalized_url": normalized_href,
                "normalized_title": normalized_article_title,
                "content_hash": content_hash,
                "source_metadata": source_metadata,
                "scores": score_bundle,
                "extracted_article": extracted_article_payload,
            },
        )
        saved_count += 1
        LOGGER.info(
            "Saved article %s for company %s (%s/%s saved for this page)",
            href,
            company["symbol"],
            saved_count,
            max_articles,
        )

    return saved_count


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


def _build_article_save_requests(
    crawled_pages: list[dict],
    jobs_by_url: dict[str, list[CompanySourceJob]],
    direct_article_jobs: list[CompanySourceJob],
) -> list[dict]:
    save_requests: list[dict] = []

    # A crawled source page may belong to one or more planned jobs, so replay
    # the page through each matching job and accumulate the article URLs that
    # should be fetched later in one shared article crawl batch.
    for page in crawled_pages:
        if not page.get("success"):
            for job in jobs_by_url.get(page["url"], []):
                LOGGER.warning(
                    "Source page crawl failed for company %s at %s: %s",
                    job["company"]["symbol"],
                    page.get("url"),
                    page.get("error"),
                )
            continue
        if not supports_source_type(page["url"], "search"):
            continue

        for job in jobs_by_url.get(page["url"], []):
            candidate_links = _filter_company_candidate_links(page["url"], page["links"], job["company"])
            save_requests.append(
                {
                    "source_page_url": page["url"],
                    "candidate_links": candidate_links,
                    "company": job["company"],
                    "search_term": job["search_term"],
                    "max_articles": MAX_ARTICLES_PER_SEARCH_PAGE,
                }
            )

    for job in direct_article_jobs:
        save_requests.append(
            {
                "source_page_url": job["url"],
                "candidate_links": [
                    {
                        "href": job["url"],
                        "text": job["search_term"],
                    }
                ],
                "company": job["company"],
                "search_term": job["search_term"],
                "max_articles": 1,
            }
        )

    return save_requests


def _collect_article_urls_to_fetch(save_requests: list[dict]) -> list[str]:
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
            if not is_allowed_source(href):
                continue
            if fetch_existing_article_by_url(href) is not None:
                continue
            if href in seen_urls:
                added_for_request += 1
                continue

            urls_to_fetch.append(href)
            seen_urls.add(href)
            added_for_request += 1

    return urls_to_fetch


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
    from collections import defaultdict

    saved_counts: dict[int, int] = defaultdict(int)
    search_jobs = [job for job in jobs if job["source_type"] == "search"]
    direct_article_jobs = [job for job in jobs if job["source_type"] == "article"]
    crawled_pages: list[dict] = []

    if search_jobs:
        jobs_by_url = group_jobs_by_url(search_jobs)
        urls = unique_job_urls(search_jobs)
        crawled_pages = crawl_articles(urls)
    else:
        jobs_by_url = {}

    save_requests = _build_article_save_requests(crawled_pages, jobs_by_url, direct_article_jobs)
    article_urls = _collect_article_urls_to_fetch(save_requests)
    fetched_articles = crawl_article_pages(article_urls) if article_urls else {}

    for request in save_requests:
        saved_counts[request["company"]["id"]] += _save_followed_article_links(
            source_page_url=request["source_page_url"],
            candidate_links=request["candidate_links"],
            company=request["company"],
            search_term=request["search_term"],
            max_articles=request["max_articles"],
            fetched_articles=fetched_articles,
        )

    return saved_counts


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


if __name__ == "__main__":
    get_company_news("Celanese Corporation")
    #get_all_company_news()
