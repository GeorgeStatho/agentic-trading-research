from pathlib import Path
import sys
from collections import defaultdict

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
from listing_page_helper import extract_listing_article_links
from Normalization import crawl_articles, extract_article
from news_normalization import build_content_hash, normalize_title, normalize_url
from source_config import (
    get_max_article_age_days,
    get_source_metadata,
    is_allowed_source,
    supports_source_type,
)
from urlFactories import INDUSTRY_NEWS_SOURCES

DATA_DIR = Path(__file__).resolve().parents[2] / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

from db_helpers import add_industry_news_article, get_all_industries, initialize_news_database


LOGGER = get_scrape_logger("industry_pipeline")


def save_followed_article_links(
    source_page_url: str,
    candidate_links: list[dict],
    industry: dict,
    max_articles: int = MAX_ARTICLES_PER_SEARCH_PAGE,
    max_age_days: int = MAX_ARTICLE_AGE_DAYS,
) -> int:
    saved_count = 0
    LOGGER.info(
        "Processing %s candidate article links for industry %s from %s",
        len(candidate_links),
        industry["name"],
        source_page_url,
    )

    for link in candidate_links:
        if saved_count >= max_articles:
            LOGGER.info(
                "Reached article limit of %s for industry %s from %s",
                max_articles,
                industry["name"],
                source_page_url,
            )
            break

        href = link.get("href")
        if not href:
            continue
        if not is_allowed_source(href):
            LOGGER.info("Skipping disallowed article URL %s for industry %s", href, industry["name"])
            continue

        article_max_age_days = get_max_article_age_days(href, max_age_days)
        normalized_href = normalize_url(href)
        existing_article = fetch_existing_article_by_url(href)

        if existing_article is not None:
            existing_published_at = existing_article.get("published_at")
            if existing_published_at is not None and not isinstance(existing_published_at, str):
                existing_published_at = str(existing_published_at)
            if not is_recent_article(existing_published_at, max_age_days=article_max_age_days):
                LOGGER.info("Skipping stale existing article %s for industry %s", href, industry["name"])
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
            LOGGER.info("Reusing existing article %s for industry %s", href, industry["name"])
        else:
            article = extract_article(href)
            if not article.success:
                record_failed_url(href, "article_follow", article.error)
                LOGGER.warning(
                    "Article follow failed for industry %s at %s: %s",
                    industry["name"],
                    href,
                    article.error,
                )
                continue
            clear_failed_url(href)
            if not is_recent_article(article.published_at, max_age_days=article_max_age_days):
                LOGGER.info("Skipping stale fetched article %s for industry %s", href, industry["name"])
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
            LOGGER.info("Fetched new article %s for industry %s", href, industry["name"])

        add_industry_news_article(
            industry_id=industry["id"],
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
                "industry_id": industry["id"],
                "industry_key": industry["industry_key"],
                "industry_name": industry["name"],
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
            "Saved article %s for industry %s (%s/%s saved for this page)",
            href,
            industry["name"],
            saved_count,
            max_articles,
        )

    return saved_count
def build_source_jobs(industries: list[dict]) -> tuple[list[str], dict[str, list[dict]]]:
    jobs_by_url: dict[str, list[dict]] = defaultdict(list)

    for industry in industries:
        for source_name, source_config in INDUSTRY_NEWS_SOURCES.items():
            url = build_source_url(industry["name"], source_config)
            if not supports_source_type(url, source_config["type"]):
                continue
            jobs_by_url[url].append(
                {
                    "industry": industry,
                    "source_name": source_name,
                    "source_type": source_config["type"],
                }
            )

    return list(jobs_by_url.keys()), jobs_by_url


def process_source_page(page: dict, industry: dict, source_type: str) -> int:
    if not page.get("success"):
        LOGGER.warning(
            "Source page crawl failed for industry %s at %s: %s",
            industry["name"],
            page.get("url"),
            page.get("error"),
        )
        return 0
    if not supports_source_type(page["url"], source_type):
        return 0

    if source_type == "listing":
        candidate_links = extract_listing_article_links(page["url"], page["links"], industry["name"])
    else:
        candidate_links = filter_article_links(page["url"], page["links"])

    return save_followed_article_links(page["url"], candidate_links, industry)


def process_crawled_pages(crawled_pages: list[dict], jobs_by_url: dict[str, list[dict]]) -> dict[int, dict[str, int]]:
    saved_counts: dict[int, dict[str, int]] = defaultdict(lambda: {"listing": 0, "search": 0})

    for page in crawled_pages:
        for job in jobs_by_url.get(page["url"], []):
            saved = process_source_page(page, job["industry"], job["source_type"])
            saved_counts[job["industry"]["id"]][job["source_type"]] += saved

    return saved_counts


def get_all_industry_news() -> None:
    initialize_news_database()
    industries = get_all_industries()
    LOGGER.info("Starting all-industry scrape for %s industries", len(industries))
    urls, jobs_by_url = build_source_jobs(industries)
    crawled_pages = crawl_articles(urls)
    saved_counts = process_crawled_pages(crawled_pages, jobs_by_url)

    for industry in industries:
        industry_counts = saved_counts.get(industry["id"], {"listing": 0, "search": 0})
        listing_saved = industry_counts["listing"]
        print(f"Saved {listing_saved} listing-page articles for {industry['name']}")

        search_saved = industry_counts["search"]
        print(f"Saved {search_saved} search-page articles for {industry['name']}")
    LOGGER.info("Finished all-industry scrape. Log file: %s", get_log_file_path())
    print(f"Scrape log written to {get_log_file_path()}")


if __name__ == "__main__":
    get_all_industry_news()
