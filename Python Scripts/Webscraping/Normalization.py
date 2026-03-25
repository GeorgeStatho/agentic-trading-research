from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import requests
import scrapy
from scrapy.crawler import CrawlerProcess
from scrapy.http import Request
from scrapy.http import Response
from scrapy.signalmanager import dispatcher
from scrapy import signals


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)


@dataclass(slots=True)
class ArticleExtractionResult:
    url: str
    title: str = ""
    text: str = ""
    success: bool = False
    error: str = ""


def _clean_text(parts: Iterable[str]) -> str:
    cleaned = []
    for part in parts:
        value = " ".join(part.split())
        if value:
            cleaned.append(value)
    return "\n".join(cleaned)


def _extract_from_response(response: Response) -> ArticleExtractionResult:
    title = response.css("title::text").get(default="").strip()

    paragraph_candidates = [
        "article p::text",
        "main p::text",
        "[role='main'] p::text",
        ".article p::text",
        ".post-content p::text",
        ".entry-content p::text",
        ".story-body p::text",
        "body p::text",
    ]

    paragraphs: list[str] = []
    for selector in paragraph_candidates:
        paragraphs = response.css(selector).getall()
        text = _clean_text(paragraphs)
        if text:
            return ArticleExtractionResult(
                url=response.url,
                title=title,
                text=text,
                success=True,
            )

    return ArticleExtractionResult(
        url=response.url,
        title=title,
        text="",
        success=False,
        error="No article text found with the current selectors.",
    )


class ArticleSpider(scrapy.Spider):
    name = "article_normalizer"
    handle_httpstatus_all = True
    custom_settings = {
        "LOG_ENABLED": True,
        "ROBOTSTXT_OBEY": False,
        "DOWNLOAD_TIMEOUT": 20,
        "USER_AGENT": DEFAULT_USER_AGENT,
    }

    def __init__(self, urls: list[str] | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = urls or []

    def start_requests(self):
        for url in self.start_urls:
            yield Request(
                url=url,
                callback=self.parse,
                errback=self.handle_failure,
                dont_filter=True,
            )

    def parse(self, response: Response):
        if response.status >= 400:
            yield {
                "url": response.url,
                "title": "",
                "text": "",
                "success": False,
                "error": f"HTTP {response.status}",
                "status": response.status,
            }
            return

        result = _extract_from_response(response)
        yield {
            "url": result.url,
            "title": result.title,
            "text": result.text,
            "success": result.success,
            "error": result.error,
            "status": response.status,
        }

    def handle_failure(self, failure):
        request = failure.request
        yield {
            "url": request.url,
            "title": "",
            "text": "",
            "success": False,
            "error": str(failure.value),
            "status": None,
        }


def crawl_articles(urls: list[str]) -> list[dict]:
    if not urls:
        return []

    items: list[dict] = []

    def _collect_item(item, response, spider):
        items.append(dict(item))

    dispatcher.connect(_collect_item, signal=signals.item_scraped)

    process = CrawlerProcess(settings={"LOG_ENABLED": True})
    process.crawl(ArticleSpider, urls=urls)
    process.start()

    dispatcher.disconnect(_collect_item, signal=signals.item_scraped)
    return items


def extract_article(url: str, timeout: int = 20) -> ArticleExtractionResult:
    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return ArticleExtractionResult(
            url=url,
            success=False,
            error=str(exc),
        )

    scrapy_response = scrapy.http.HtmlResponse(
        url=response.url,
        body=response.content,
        encoding=response.encoding or "utf-8",
    )
    return _extract_from_response(scrapy_response)


if __name__ == "__main__":
    demo_urls = ["https://www.economist.com/",
    "https://www.bloomberg.com/",
    "https://www.reuters.com/markets/",
    "https://www.marketwatch.com/",
    "https://finance.yahoo.com/",
    "https://www.wsj.com/news/business",
    "https://www.ft.com/markets",
    "https://www.cnbc.com/finance/",
    "https://www.investing.com/",
    "https://www.fool.com/",
    "https://www.barrons.com/",
    "https://www.morningstar.com/",
    "https://www.thestreet.com/",
    "https://www.zacks.com/",
    "https://www.businessinsider.com/markets"]
    print("Starting crawl...", demo_urls)

    items = crawl_articles(demo_urls)
    print("Items found:", len(items))

    for item in items:
        print(item["url"])
        print(item["title"])
        print(item["text"][:500])
