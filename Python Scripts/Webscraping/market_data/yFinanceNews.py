import yfinance as yf

from sources.source_config import is_allowed_source


def get_company_news_items(company: str, count: int = 10) -> list[dict]:
    # Ticker.get_news returns [] for many tickers now; Search still carries the news, so try it first.
    try:
        items = yf.Search(company, news_count=count).news
        if items:
            return items
    except Exception:
        pass
    return yf.Ticker(company).get_news(count=count)


def extract_title_url_publisher(news_data: list[dict]) -> list[tuple[str, str, str]]:
    triples = []

    for item in news_data:
        content = item.get("content")
        if content is None:
            # Flat shape from yf.Search
            title, url, publisher = item.get("title"), item.get("link"), item.get("publisher")
        else:
            title = content.get("title")
            click_through = content.get("clickThroughUrl") or {}
            canonical = content.get("canonicalUrl") or {}
            url = (
                click_through.get("url")
                or canonical.get("url")
                or content.get("previewUrl")
            )
            publisher = (content.get("provider") or {}).get("displayName")

        if title and url and is_allowed_source(url):
            triples.append((title, url, publisher or ""))
    return triples


def extract_title_and_url(news_data: list[dict]) -> list[tuple[str, str]]:
    return [(title, url) for title, url, _ in extract_title_url_publisher(news_data)]


def getCompanyNews(company: str):
    news = get_company_news_items(company)
    return extract_title_and_url(news)


def extractTitleandUrl(news_data: list):
    return extract_title_and_url(news_data)
