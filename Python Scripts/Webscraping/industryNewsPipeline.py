from pathlib import Path
import sys
from urllib.parse import quote_plus
from urllib.parse import urlparse

from Normalization import crawl_articles, extract_article
from urlFactories import INDUSTRY_SEARCH_URLS

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


def get_industry_news(urls: list[str]) -> list[dict]:
    industry_news = crawl_articles(urls)
    for article in industry_news:
        print(article["title"])
        print(article["url"])
        print(filter_article_links(article["url"], article["links"]))
    return industry_news


def save_followed_article_links(search_page: dict, industry: dict, max_articles: int = MAX_ARTICLES_PER_SEARCH_PAGE) -> int:
    saved_count = 0
    filtered_links = filter_article_links(search_page["url"], search_page["links"])

    for link in filtered_links:
        if saved_count >= max_articles:
            break

        href = link.get("href")
        if not href:
            continue

        article = extract_article(href)
        if not article.success:
            continue

        title = article.title or link.get("text") or href
        article_key = href
        source = urlparse(href).netloc.lower()
        add_industry_news_article(
            industry_id=industry["id"],
            source=source,
            article_key=article_key,
            title=title,
            source_url=href,
            source_page_url=search_page["url"],
            summary=link.get("text"),
            body=article.text,
            raw_json={
                "industry_id": industry["id"],
                "industry_key": industry["industry_key"],
                "industry_name": industry["name"],
                "search_page_url": search_page["url"],
                "link": link,
                "extracted_article": {
                    "url": article.url,
                    "title": article.title,
                    "text": article.text,
                    "success": article.success,
                    "error": article.error,
                },
            },
        )
        saved_count += 1

    return saved_count


def build_search_urls(industry: str) -> list[str]:
    encoded_industry = quote_plus(industry)
    urls: list[str] = []
    for source_name, base_url in INDUSTRY_SEARCH_URLS.items():
        if source_name == "yahoo_finance":
            urls.append(base_url)
        elif "{query}" in base_url:
            urls.append(base_url.format(query=encoded_industry))
        else:
            urls.append(f"{base_url}{encoded_industry}")
    return urls


def get_all_industry_news() -> list[dict]:
    initialize_news_database()

    all_urls: list[str] = []
    seen_urls: set[str] = set()
    url_to_industry: dict[str, dict] = {}

    for industry in get_all_industries():
        for url in build_search_urls(industry["name"]):
            if url not in seen_urls:
                seen_urls.add(url)
                all_urls.append(url)
            url_to_industry[url] = industry

    industry_news = get_industry_news(all_urls)
    for search_page in industry_news:
        industry = url_to_industry.get(search_page["url"])
        if industry is None:
            continue
        saved_count = save_followed_article_links(search_page, industry)
        print(f"Saved {saved_count} followed articles for {industry['name']}")

    return industry_news


if __name__ == "__main__":
    get_all_industry_news()
