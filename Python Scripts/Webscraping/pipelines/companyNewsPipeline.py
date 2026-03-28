from pathlib import Path
import sys
from collections import defaultdict
import re

WEBSCRAPING_DIR = Path(__file__).resolve().parents[1]
if str(WEBSCRAPING_DIR) not in sys.path:
    sys.path.append(str(WEBSCRAPING_DIR))

from core.CommonPipeline import (
    MAX_ARTICLES_PER_SEARCH_PAGE,
    MAX_ARTICLE_AGE_DAYS,
    build_source_url,
    clear_failed_url,
    compute_article_scores,
    fetch_existing_article_by_url,
    filter_article_links,
    is_recent_article,
    record_failed_url,
)
from core.scrape_logging import get_log_file_path, get_scrape_logger
from Normalization import crawl_articles, extract_article
from news_normalization import build_content_hash, normalize_title, normalize_url
from source_config import (
    get_max_article_age_days,
    get_source_metadata,
    is_allowed_source,
    supports_source_type,
)
from urlFactories import COMPANY_NEWS_SOURCES

DATA_DIR = Path(__file__).resolve().parents[2] / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

from db_helpers import add_company_news_article, get_all_companies, initialize_news_database


LOGGER = get_scrape_logger("company_pipeline")
COMPANY_NAME_SUFFIXES = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "plc",
    "ltd",
    "limited",
    "lp",
    "llc",
    "sa",
    "nv",
    "se",
    "holdings",
    "holding",
    "group",
}
CNBC_BLACKLISTED_PATH_FRAGMENTS = (
    "/investingclub/video/",
    "/pro/news/",
    "/pro/options-investing/",
    "/application/pro/",
)


def build_company_search_terms(company: dict) -> list[str]:
    # Keep company discovery narrowly focused on the formal company name so
    # we do not explode the number of search URLs with weak alternate queries.
    company_name = " ".join((company.get("name") or "").split()).strip()
    return [company_name] if company_name else []


def normalize_match_text(value: str | None) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
    return " ".join(cleaned.split())


def build_company_match_variants(company: dict) -> set[str]:
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


def is_blacklisted_cnbc_link(href: str) -> bool:
    normalized_href = href.lower()
    return "cnbc.com" in normalized_href and any(
        fragment in normalized_href for fragment in CNBC_BLACKLISTED_PATH_FRAGMENTS
    )


def filter_company_candidate_links(page_url: str, links: list[dict], company: dict) -> list[dict]:
    # Start from the shared source/article-pattern filter, then apply
    # company-specific relevance checks to remove generic market pages.
    base_candidates = filter_article_links(page_url, links)
    if not base_candidates:
        return []

    variants = build_company_match_variants(company)
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

        normalized_text = normalize_match_text(link.get("text"))
        normalized_href = normalize_match_text(href)
        if any(variant in normalized_text or variant in normalized_href for variant in variants):
            filtered.append(link)
            continue

        LOGGER.info(
            "Skipping weak company match URL %s for company %s because the link text did not match the company name",
            href,
            company["symbol"],
        )

    return filtered


def save_followed_article_links(
    source_page_url: str,
    candidate_links: list[dict],
    company: dict,
    search_term: str,
    max_articles: int = MAX_ARTICLES_PER_SEARCH_PAGE,
    max_age_days: int = MAX_ARTICLE_AGE_DAYS,
) -> int:
    saved_count = 0
    # Follow the filtered result links one level deep, reuse any article that
    # already exists in the DB, and save new articles that pass recency checks.
    LOGGER.info(
        "Processing %s candidate article links for company %s from %s",
        len(candidate_links),
        company["symbol"],
        source_page_url,
    )

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
            article = extract_article(href)
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
def build_source_jobs(companies: list[dict]) -> tuple[list[str], dict[str, list[dict]]]:
    jobs_by_url: dict[str, list[dict]] = defaultdict(list)

    # Precompute every search-page job so the spider can crawl all source
    # pages in one Scrapy run and avoid restarting the reactor repeatedly.
    for company in companies:
        for search_term in build_company_search_terms(company):
            for source_name, source_config in COMPANY_NEWS_SOURCES.items():
                url = build_source_url(search_term, source_config)
                if not supports_source_type(url, source_config["type"]):
                    continue
                jobs_by_url[url].append(
                    {
                        "company": company,
                        "source_name": source_name,
                        "search_term": search_term,
                    }
                )

    return list(jobs_by_url.keys()), jobs_by_url


def process_source_page(page: dict, company: dict, search_term: str) -> int:
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
    candidate_links = filter_company_candidate_links(page["url"], page["links"], company)
    return save_followed_article_links(page["url"], candidate_links, company, search_term)


def process_crawled_pages(crawled_pages: list[dict], jobs_by_url: dict[str, list[dict]]) -> dict[int, int]:
    saved_counts: dict[int, int] = defaultdict(int)

    # A crawled source page may belong to one or more planned jobs, so replay
    # the page through each matching job and accumulate saved-article counts.
    for page in crawled_pages:
        for job in jobs_by_url.get(page["url"], []):
            saved = process_source_page(page, job["company"], job["search_term"])
            saved_counts[job["company"]["id"]] += saved

    return saved_counts


def find_company(company_identifier: str) -> dict | None:
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
    company = find_company(company_identifier)
    if company is None:
        raise ValueError(f"Company not found for identifier: {company_identifier}")

    LOGGER.info("Starting single-company scrape for %s (%s)", company["name"], company["symbol"])
    # For single-company runs we still use the same bulk-crawl flow so the
    # behavior matches the full pipeline and logs stay comparable.
    urls, jobs_by_url = build_source_jobs([company])
    crawled_pages = crawl_articles(urls)
    saved_counts = process_crawled_pages(crawled_pages, jobs_by_url)
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
    urls, jobs_by_url = build_source_jobs(companies)
    crawled_pages = crawl_articles(urls)
    saved_counts = process_crawled_pages(crawled_pages, jobs_by_url)

    for company in companies:
        saved = saved_counts.get(company["id"], 0)
        print(f"Saved {saved} articles for {company['name']} ({company['symbol']})")
    LOGGER.info("Finished all-company scrape. Log file: %s", get_log_file_path())
    print(f"Scrape log written to {get_log_file_path()}")


if __name__ == "__main__":
    #get_company_news("Celanese Corporation")
    get_all_company_news()
