from __future__ import annotations

import scrapy
from scrapy import signals
from scrapy.crawler import CrawlerProcess
from scrapy.http import Request
from scrapy.http import Response
from scrapy.signalmanager import dispatcher

from article_extraction import DEFAULT_USER_AGENT, extract_from_response


def extract_links(response: Response) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen_hrefs: set[str] = set()

    for link in response.css("a"):
        href = link.css("::attr(href)").get()
        if not href:
            continue

        absolute_href = response.urljoin(href.strip())
        if not absolute_href or absolute_href in seen_hrefs:
            continue

        link_text = " ".join(part.strip() for part in link.css("::text").getall() if part.strip())
        links.append(
            {
                "href": absolute_href,
                "text": link_text,
            }
        )
        seen_hrefs.add(absolute_href)

    return links


class ArticleSpider(scrapy.Spider):
    name = "article_normalizer"
    handle_httpstatus_all = True
    custom_settings = {
        "LOG_ENABLED": True,
    "ROBOTSTXT_OBEY": False,
    "DOWNLOAD_TIMEOUT": 20,
    "USER_AGENT": DEFAULT_USER_AGENT,
    "COOKIES_ENABLED": True,
    "DOWNLOAD_DELAY": 2.0,
    "RANDOMIZE_DOWNLOAD_DELAY": True,
    "CONCURRENT_REQUESTS": 4,
    "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
    "RETRY_TIMES": 3,
    "AUTOTHROTTLE_ENABLED": True,
    "AUTOTHROTTLE_START_DELAY": 2.0,
    "AUTOTHROTTLE_MAX_DELAY": 10.0,
    "DEFAULT_REQUEST_HEADERS": {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    },
}

    def __init__(self, urls: list[str] | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = urls or []

    def start_requests(self):
        for url in self.start_urls:
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.google.com/",
                "Upgrade-Insecure-Requests": "1",
                }
            yield Request(
                url=url,
                headers=headers,
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

        result = extract_from_response(response)
        yield {
            "url": result.url,
            "title": result.title,
            "text": result.text,
            "links": extract_links(response),
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
            "links": [],
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
