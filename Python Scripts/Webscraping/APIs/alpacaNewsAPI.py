from __future__ import annotations

import os
from collections import OrderedDict
from threading import Lock
from typing import Any

from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest
from bs4 import BeautifulSoup

from core.scrape_logging import get_scrape_logger
from engine.article_extraction import clean_text
from processing.news_normalization import normalize_url
from services.trading_config import AlpacaSettings

LOGGER = get_scrape_logger("alpaca_news_api")

CONTENT_CACHE_LIMIT = 500
# get_stock_data stores each item here so get_article_content never has to scrape benzinga.com.
_CONTENT_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_CACHE_LOCK = Lock()
_NEWS_CLIENT: NewsClient | bool | None = None  # None = not built yet, False = no credentials


def _get_news_client() -> NewsClient | None:
    # Build the client lazily so a missing key never breaks import.
    global _NEWS_CLIENT
    if _NEWS_CLIENT is None:
        settings = AlpacaSettings.from_env()
        if settings.api_key and settings.api_secret:
            # Same OAuth-token clearing as alpaca_base, which would otherwise override the key pair.
            os.environ.pop("APCA_OAUTH_TOKEN", None)
            os.environ.pop("ALPACA_OAUTH_TOKEN", None)
            _NEWS_CLIENT = NewsClient(settings.api_key, settings.api_secret)
        else:
            LOGGER.warning("Alpaca credentials missing; Alpaca news source disabled.")
            _NEWS_CLIENT = False
    return _NEWS_CLIENT or None


def _html_to_text(html: str) -> str:
    return clean_text(BeautifulSoup(html or "", "html.parser").get_text("\n").splitlines())


def get_stock_data(symbol: str, limit: int = 10) -> dict[str, Any]:
    client = _get_news_client()
    if client is None:
        return {"data": {"articles": []}}

    result = client.get_news(
        NewsRequest(symbols=symbol, limit=limit, include_content=True, exclude_contentless=True)
    )
    items = (result.data if hasattr(result, "data") else result).get("news", [])

    articles = []
    with _CACHE_LOCK:
        for item in items:
            url = str(item.url or "").strip()
            if not url:
                continue
            key = normalize_url(url)
            _CONTENT_CACHE.pop(key, None)
            _CONTENT_CACHE[key] = {
                "title": item.headline,
                "body": _html_to_text(item.content) or _html_to_text(item.summary),
                "published_at": item.created_at.isoformat() if item.created_at else "",
                "url": url,
                "symbols": list(item.symbols or []),
                "source": item.source,
                "author": item.author,
            }
            while len(_CONTENT_CACHE) > CONTENT_CACHE_LIMIT:
                _CONTENT_CACHE.popitem(last=False)
            articles.append({"title": item.headline, "url": url})
    return {"data": {"articles": articles}}


def get_article_content(url: str) -> dict[str, Any]:
    with _CACHE_LOCK:
        cached = _CONTENT_CACHE.get(normalize_url(url))
    if cached is None:
        # Raising would make page_fetch crawl benzinga.com; an empty body is kept as success=False instead.
        return {"data": {"title": "", "body": "", "published_at": "", "url": url, "symbols": [], "source": "", "author": ""}}
    return {"data": dict(cached)}
