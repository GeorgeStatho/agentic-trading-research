from pathlib import Path
import sys

import yfinance as yf


if __package__ in {None, ""}:
    WEBSCRAPING_DIR = Path(__file__).resolve().parents[1]
    if str(WEBSCRAPING_DIR) not in sys.path:
        sys.path.append(str(WEBSCRAPING_DIR))

from source_config import is_allowed_source


def get_company_news_items(company: str, count: int = 10) -> list[dict]:
    ticker = yf.Ticker(company)
    return ticker.get_news(count=count)


def extract_title_and_url(news_data: list[dict]) -> list[tuple[str, str]]:
    pairs = []

    for item in news_data:
        content = item.get("content", {})

        title = content.get("title")

        click_through = content.get("clickThroughUrl") or {}
        canonical = content.get("canonicalUrl") or {}

        url = (
            click_through.get("url")
            or canonical.get("url")
            or content.get("previewUrl")
        )

        if title and url and is_allowed_source(url):
            pairs.append((title, url))
    return pairs


def getCompanyNews(company: str):
    news = get_company_news_items(company)
    return extract_title_and_url(news)


def extractTitleandUrl(news_data: list):
    return extract_title_and_url(news_data)
