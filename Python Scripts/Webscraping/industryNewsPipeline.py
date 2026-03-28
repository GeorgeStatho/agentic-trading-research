from pathlib import Path
import sys
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from collections import defaultdict
from urllib.parse import quote_plus
from urllib.parse import urlparse

from listing_page_helper import extract_listing_article_links
from Normalization import crawl_articles, extract_article
from news_normalization import build_content_hash, normalize_title, normalize_url
from urlFactories import INDUSTRY_NEWS_SOURCES

DATA_DIR = Path(__file__).resolve().parents[2] / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

from db_helpers import add_industry_news_article, get_all_industries, initialize_news_database


ARTICLE_PATTERNS_BY_DOMAIN = {
    "cnbc.com": ["/202", "/video/", "/pro/"],
    "investing.com": ["/news/"],
    "marketwatch.com": ["/story/"],
    "barrons.com": ["/articles/"],
    "fool.com": ["/investing/", "/earnings/", "/research/", "/news/"],
    "morningstar.com": ["/news/", "/markets/", "/stocks/"],
    "businessinsider.com": ["/news/", "/stock-market/", "/economy/"],
    "markets.businessinsider.com": ["/news/"],
    "finance.yahoo.com": ["/news/"],
}
MAX_ARTICLES_PER_SEARCH_PAGE = 10
MAX_ARTICLE_AGE_DAYS = 14


def filter_article_links(page_url: str, links: list[dict]) -> list[dict]:
    domain = urlparse(page_url).netloc.lower()
    patterns = []
    for candidate_domain, candidate_patterns in ARTICLE_PATTERNS_BY_DOMAIN.items():
        if candidate_domain in domain:
            patterns = candidate_patterns
            break

    filtered_links: list[dict] = []
    seen_hrefs: set[str] = set()
    for link in links:
        href = link.get("href", "")
        if not href or href in seen_hrefs:
            continue
        if patterns and not any(pattern in href for pattern in patterns):
            continue
        filtered_links.append(link)
        seen_hrefs.add(href)

    return filtered_links


def is_recent_article(published_at: str, max_age_days: int = MAX_ARTICLE_AGE_DAYS) -> bool:
    if not published_at:
        return False

    try:
        parsed = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return False

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return parsed >= cutoff


def save_followed_article_links(
    source_page_url: str,
    candidate_links: list[dict],
    industry: dict,
    max_articles: int = MAX_ARTICLES_PER_SEARCH_PAGE,
    max_age_days: int = MAX_ARTICLE_AGE_DAYS,
) -> int:
    saved_count = 0

    for link in candidate_links:
        if saved_count >= max_articles:
            break

        href = link.get("href")
        if not href:
            continue

        article = extract_article(href)
        if not article.success:
            continue
        if not is_recent_article(article.published_at, max_age_days=max_age_days):
            continue

        title = article.title or link.get("text") or href
        normalized_href = normalize_url(href)
        article_key = normalized_href or href
        source = urlparse(href).netloc.lower()
        normalized_article_title = normalize_title(title)
        content_hash = build_content_hash(article.text)

        add_industry_news_article(
            industry_id=industry["id"],
            source=source,
            article_key=article_key,
            title=title,
            source_url=href,
            source_page_url=source_page_url,
            summary=link.get("text"),
            body=article.text,
            published_at=article.published_at or None,
            raw_json={
                "industry_id": industry["id"],
                "industry_key": industry["industry_key"],
                "industry_name": industry["name"],
                "source_page_url": source_page_url,
                "link": link,
                "normalized_url": normalized_href,
                "normalized_title": normalized_article_title,
                "content_hash": content_hash,
                "extracted_article": {
                    "url": article.url,
                    "title": article.title,
                    "text": article.text,
                    "published_at": article.published_at,
                    "success": article.success,
                    "error": article.error,
                },
            },
        )
        saved_count += 1

    return saved_count


def build_source_url(industry_name: str, source_config: dict) -> str:
    encoded_industry = quote_plus(industry_name)
    base_url = source_config["url"]
    if "{query}" in base_url:
        return base_url.format(query=encoded_industry)
    return base_url


def build_source_jobs(industries: list[dict]) -> tuple[list[str], dict[str, list[dict]]]:
    jobs_by_url: dict[str, list[dict]] = defaultdict(list)

    for industry in industries:
        for source_name, source_config in INDUSTRY_NEWS_SOURCES.items():
            url = build_source_url(industry["name"], source_config)
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
    urls, jobs_by_url = build_source_jobs(industries)
    crawled_pages = crawl_articles(urls)
    saved_counts = process_crawled_pages(crawled_pages, jobs_by_url)

    for industry in industries:
        industry_counts = saved_counts.get(industry["id"], {"listing": 0, "search": 0})
        listing_saved = industry_counts["listing"]
        print(f"Saved {listing_saved} listing-page articles for {industry['name']}")

        search_saved = industry_counts["search"]
        print(f"Saved {search_saved} search-page articles for {industry['name']}")


if __name__ == "__main__":
    get_all_industry_news()
