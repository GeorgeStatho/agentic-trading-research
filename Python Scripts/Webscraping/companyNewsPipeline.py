from pathlib import Path
import sys
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from collections import defaultdict
from urllib.parse import quote_plus
from urllib.parse import urlparse

from Normalization import crawl_articles, extract_article
from news_normalization import build_content_hash, normalize_title, normalize_url
from urlFactories import COMPANY_NEWS_SOURCES

DATA_DIR = Path(__file__).resolve().parents[2] / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

from db_helpers import add_company_news_article, get_all_companies, initialize_news_database


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


def build_company_search_terms(company: dict) -> list[str]:
    raw_terms = [
        company.get("name"),
        company.get("symbol"),
        f"{company.get('name', '')} stock".strip(),
        f"{company.get('name', '')} {company.get('symbol', '')}".strip(),
    ]

    deduped_terms: list[str] = []
    seen_terms: set[str] = set()
    for term in raw_terms:
        cleaned = " ".join((term or "").split()).strip()
        if not cleaned:
            continue
        normalized = cleaned.lower()
        if normalized in seen_terms:
            continue
        seen_terms.add(normalized)
        deduped_terms.append(cleaned)

    return deduped_terms


def save_followed_article_links(
    source_page_url: str,
    candidate_links: list[dict],
    company: dict,
    search_term: str,
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

        add_company_news_article(
            company_id=company["id"],
            source=source,
            article_key=article_key,
            title=title,
            source_url=href,
            source_page_url=source_page_url,
            summary=link.get("text"),
            body=article.text,
            published_at=article.published_at or None,
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


def build_source_url(search_term: str, source_config: dict) -> str:
    encoded_search_term = quote_plus(search_term)
    base_url = source_config["url"]
    if "{query}" in base_url:
        return base_url.format(query=encoded_search_term)
    return base_url


def build_source_jobs(companies: list[dict]) -> tuple[list[str], dict[str, list[dict]]]:
    jobs_by_url: dict[str, list[dict]] = defaultdict(list)

    for company in companies:
        for search_term in build_company_search_terms(company):
            for source_name, source_config in COMPANY_NEWS_SOURCES.items():
                url = build_source_url(search_term, source_config)
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
        return 0

    candidate_links = filter_article_links(page["url"], page["links"])
    return save_followed_article_links(page["url"], candidate_links, company, search_term)


def process_crawled_pages(crawled_pages: list[dict], jobs_by_url: dict[str, list[dict]]) -> dict[int, int]:
    saved_counts: dict[int, int] = defaultdict(int)

    for page in crawled_pages:
        for job in jobs_by_url.get(page["url"], []):
            saved = process_source_page(page, job["company"], job["search_term"])
            saved_counts[job["company"]["id"]] += saved

    return saved_counts


def get_all_company_news() -> None:
    initialize_news_database()
    companies = get_all_companies()
    urls, jobs_by_url = build_source_jobs(companies)
    crawled_pages = crawl_articles(urls)
    saved_counts = process_crawled_pages(crawled_pages, jobs_by_url)

    for company in companies:
        saved = saved_counts.get(company["id"], 0)
        print(f"Saved {saved} articles for {company['name']} ({company['symbol']})")


if __name__ == "__main__":
    get_all_company_news()
