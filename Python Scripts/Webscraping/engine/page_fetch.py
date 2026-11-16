from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import requests
from scrapy.http import HtmlResponse

from APIs.alpacaNewsAPI import get_article_content as get_alpaca_article_content
from APIs.foolAPI import get_article_content as get_fool_article_content
from APIs.gurufocusAPI import get_article_content as get_gurufocus_article_content
from APIs.InvestingAPI import get_article_content as get_investing_article_content
from APIs.morningStarAPI import get_article_content as get_morningstar_article_content
from core.scrape_logging import get_scrape_logger
from engine.article_extraction import (
    DEFAULT_USER_AGENT,
    ArticleContentUnavailable,
    ArticleExtractionResult,
    extract_from_response,
)
from engine.article_scraper import crawl_article_pages, crawl_articles

LOGGER = get_scrape_logger("page_fetch")


def _get_yahoo_article_content(url: str) -> dict[str, Any]:
    # Scrapy's Twisted rejects Yahoo's oversized Link response header, so Yahoo articles skip the crawler.
    response = requests.get(
        url, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}, timeout=20
    )
    response.raise_for_status()
    article = extract_from_response(HtmlResponse(url=response.url, body=response.content))
    return {
        "data": {"url": article.url, "title": article.title, "body": article.text, "published_at": article.published_at}
    }

ARTICLE_CONTENT_FETCHERS: tuple[tuple[str, Callable[[str], dict[str, Any]]], ...] = (
    ("investor.example.com", get_fool_article_content),
    ("calendar.example.com", get_investing_article_content),
    ("research.example.com", get_morningstar_article_content),
    ("value.example.com", get_gurufocus_article_content),
    ("benzinga.com", get_alpaca_article_content),
    ("finance.yahoo.com", _get_yahoo_article_content),
)


def _get_article_content_fetcher(url: str) -> Callable[[str], dict[str, Any]] | None:
    hostname = (urlsplit(str(url or "")).hostname or "").lower()
    for domain, fetcher in ARTICLE_CONTENT_FETCHERS:
        if hostname == domain or hostname.endswith(f".{domain}"):
            return fetcher
    return None


def _extract_article_result_from_api_payload(payload: dict[str, Any], fallback_url: str) -> ArticleExtractionResult:
    # Extract the article result from api payload from the raw response and return a stable value.
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return ArticleExtractionResult(
            url=fallback_url,
            success=False,
            error="API article extractor returned an unexpected payload shape.",
        )

    title = str(data.get("title") or "").strip()
    body = str(data.get("body") or "").strip()
    published_at = str(data.get("published_at") or "").strip()
    url = str(data.get("url") or fallback_url).strip() or fallback_url

    if not title or not body:
        return ArticleExtractionResult(
            url=url,
            title=title,
            text=body,
            published_at=published_at,
            success=False,
            error="API article extractor returned empty title or body.",
        )

    return ArticleExtractionResult(
        url=url,
        title=title,
        text=body,
        published_at=published_at,
        success=True,
    )


API_FETCH_WORKERS = 4


def _fetch_via_api(url: str) -> tuple[ArticleExtractionResult | None, bool]:
    """The API extractor's result for one URL, and whether the crawler should try it instead."""
    fetcher = _get_article_content_fetcher(url)
    if fetcher is None:
        return None, True
    try:
        result = _extract_article_result_from_api_payload(fetcher(url), url)
    except ArticleContentUnavailable:
        LOGGER.info("No API content yet for %s; leaving it for the next run", url)
        return None, False
    except Exception as exc:
        LOGGER.warning("API article extractor failed for %s: %s", url, exc)
        return None, True
    if result.success:
        LOGGER.info("Fetched article via API extractor for %s", url)
    return result, False


class LiveFetcher:
    def source_pages(self, urls: list[str]) -> list[dict]:
        if not urls:
            return []
        try:
            return crawl_articles(urls)
        except Exception as exc:
            LOGGER.warning("Source page crawl failed for %d urls: %s", len(urls), exc)
            return []

    def articles(self, urls: list[str]) -> dict[str, ArticleExtractionResult]:
        fetched_articles: dict[str, ArticleExtractionResult] = {}
        fallback_urls: list[str] = []

        # The API extractors are network-bound and independent; the crawler fallback already runs 4 workers.
        with ThreadPoolExecutor(max_workers=API_FETCH_WORKERS) as pool:
            outcomes = list(pool.map(_fetch_via_api, urls))
        for url, (result, fall_back) in zip(urls, outcomes):
            if result is not None:
                fetched_articles[url] = result
            if fall_back:
                fallback_urls.append(url)

        if fallback_urls:
            try:
                fetched_articles.update(crawl_article_pages(fallback_urls))
            except Exception as exc:
                LOGGER.warning("Article crawl failed for %d urls: %s", len(fallback_urls), exc)

        return fetched_articles

    def feed(self, url: str) -> str | None:
        request = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; StockTradingExperiment/1.0; +https://newswire.example.com/)",
                "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            LOGGER.warning("RSS feed fetch failed for %s: %s", url, exc)
            return None


class FixtureFetcher:
    def __init__(
        self,
        *,
        pages: dict[str, dict] | None = None,
        articles: dict[str, ArticleExtractionResult] | None = None,
        feeds: dict[str, str] | None = None,
    ) -> None:
        self._pages = pages or {}
        self._articles = articles or {}
        self._feeds = feeds or {}
        self.requested: list[str] = []

    def source_pages(self, urls: list[str]) -> list[dict]:
        self.requested.extend(urls)
        return [self._pages[u] for u in urls if u in self._pages]

    def articles(self, urls: list[str]) -> dict[str, ArticleExtractionResult]:
        self.requested.extend(urls)
        return {u: self._articles[u] for u in urls if u in self._articles}

    def feed(self, url: str) -> str | None:
        self.requested.append(url)
        return self._feeds.get(url)


_LIVE_FETCHER = LiveFetcher()
_CURRENT_FETCHER: ContextVar[Any] = ContextVar("current_fetcher", default=None)


def current_fetcher():
    return _CURRENT_FETCHER.get() or _LIVE_FETCHER


@contextmanager
def use_fetcher(fetcher) -> Iterator[Any]:
    token = _CURRENT_FETCHER.set(fetcher)
    try:
        yield fetcher
    finally:
        _CURRENT_FETCHER.reset(token)


def fetch_source_pages(urls: list[str]) -> list[dict]:
    return current_fetcher().source_pages(urls)


def fetch_articles(urls: list[str]) -> dict[str, ArticleExtractionResult]:
    return current_fetcher().articles(urls)


def fetch_feed(url: str) -> str | None:
    return current_fetcher().feed(url)
