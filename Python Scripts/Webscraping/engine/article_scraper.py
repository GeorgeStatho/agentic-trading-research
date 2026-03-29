from __future__ import annotations

import os
import sys
import threading
import scrapy
from scrapy import signals
from scrapy.crawler import CrawlerProcess
from scrapy.http import Request
from scrapy.http import Response
from scrapy.signalmanager import dispatcher

from article_extraction import ArticleExtractionResult, DEFAULT_USER_AGENT, extract_from_response
from barrons_extractor import extract_barrons_search_links, response_looks_like_barrons_search
from cnbc_extractor import extract_cnbc_search_links, response_looks_like_cnbc_search
from core.scrape_logging import get_log_file_path, get_scrape_logger, get_scrapy_log_settings
from fool_extractor import extract_fool_quote_links, response_looks_like_fool_quote
from investing_extractor import extract_investing_search_links, response_looks_like_investing_search
from marketwatch_extractor import extract_marketwatch_search_links, response_looks_like_marketwatch_search

LOGGER = get_scrape_logger("article_scraper")


class _KeyboardStopMonitor:
    def __init__(self, process: CrawlerProcess):
        self.process = process
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not sys.stdin or not sys.stdin.isatty():
            return

        self._thread = threading.Thread(target=self._watch_for_stop_key, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)

    def _request_stop(self, key_label: str) -> None:
        if self._stop_event.is_set():
            return

        self._stop_event.set()
        LOGGER.warning("Keyboard stop requested with %s; stopping crawl early", key_label)
        print(f"\nStopping crawl early because {key_label} was pressed...")
        from twisted.internet import reactor

        reactor.callFromThread(self.process.stop)

    def _watch_for_stop_key(self) -> None:
        if os.name == "nt":
            self._watch_windows()
            return
        self._watch_posix()

    def _watch_windows(self) -> None:
        import msvcrt

        while not self._stop_event.is_set():
            if not msvcrt.kbhit():
                self._stop_event.wait(0.2)
                continue

            pressed = msvcrt.getwch()
            if pressed in {"q", "Q"}:
                self._request_stop("q")
                return
            if pressed == "\x1b":
                self._request_stop("Esc")
                return

    def _watch_posix(self) -> None:
        import select
        import termios
        import tty

        stdin_fd = sys.stdin.fileno()
        original_settings = termios.tcgetattr(stdin_fd)
        try:
            tty.setcbreak(stdin_fd)
            while not self._stop_event.is_set():
                readable, _, _ = select.select([sys.stdin], [], [], 0.2)
                if not readable:
                    continue

                pressed = sys.stdin.read(1)
                if pressed in {"q", "Q"}:
                    self._request_stop("q")
                    return
                if pressed == "\x1b":
                    self._request_stop("Esc")
                    return
        finally:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, original_settings)


def extract_search_links(response: Response) -> list[dict[str, str]]:
    if response_looks_like_barrons_search(response):
        return extract_barrons_search_links(response)
    if response_looks_like_marketwatch_search(response):
        return extract_marketwatch_search_links(response)
    if response_looks_like_cnbc_search(response):
        return extract_cnbc_search_links(response)
    if response_looks_like_investing_search(response):
        return extract_investing_search_links(response)
    if response_looks_like_fool_quote(response):
        return extract_fool_quote_links(response)
    return extract_links(response)


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
            self.logger.info("Requesting %s", url)
            yield Request(
                url=url,
                headers=headers,
                callback=self.parse,
                errback=self.handle_failure,
                dont_filter=True,
            )

    def parse(self, response: Response):
        if response.status >= 400:
            self.logger.warning("Non-200 response for %s: HTTP %s", response.url, response.status)
            yield {
                "request_url": response.request.url,
                "url": response.url,
                "title": "",
                "text": "",
                "published_at": "",
                "success": False,
                "error": f"HTTP {response.status}",
                "status": response.status,
            }
            return

        result = extract_from_response(response)
        if result.success:
            self.logger.info("Scraped %s successfully", response.url)
        else:
            self.logger.warning("Extraction issue for %s: %s", response.url, result.error)

        discovered_links = extract_search_links(response)
        yield {
            "request_url": response.request.url,
            "url": result.url,
            "title": result.title,
            "text": result.text,
            "published_at": result.published_at,
            "links": discovered_links,
            "success": result.success,
            "error": result.error,
            "status": response.status,
        }

    def handle_failure(self, failure):
        request = failure.request
        self.logger.error("Request failed for %s: %s", request.url, failure.value)
        return {
            "request_url": request.url,
            "url": request.url,
            "title": "",
            "text": "",
            "published_at": "",
            "links": [],
            "success": False,
            "error": str(failure.value),
            "status": None,
        }


def crawl_articles(urls: list[str]) -> list[dict]:
    if not urls:
        return []

    items: list[dict] = []
    log_file = get_log_file_path()
    LOGGER.info("Starting source-page crawl for %s URLs", len(urls))

    def _collect_item(item, response, spider):
        items.append(dict(item))

    dispatcher.connect(_collect_item, signal=signals.item_scraped)

    process = CrawlerProcess(settings=get_scrapy_log_settings())
    process.crawl(ArticleSpider, urls=urls)
    stop_monitor = _KeyboardStopMonitor(process)
    if sys.stdin and sys.stdin.isatty():
        LOGGER.info("Keyboard stop enabled: press q, Esc, or Ctrl+C to stop the crawl early")
        print("Press q, Esc, or Ctrl+C to stop the crawl early.")

    stop_monitor.start()
    try:
        process.start()
    except KeyboardInterrupt:
        LOGGER.warning("KeyboardInterrupt received; stopping crawl early")
        print("\nStopping crawl early because Ctrl+C was pressed...")
        process.stop()
    finally:
        stop_monitor.stop()

    dispatcher.disconnect(_collect_item, signal=signals.item_scraped)
    success_count = sum(1 for item in items if item.get("success"))
    failure_count = len(items) - success_count
    LOGGER.info(
        "Finished source-page crawl for %s URLs: %s success, %s failure",
        len(urls),
        success_count,
        failure_count,
    )
    print(f"Scrape log written to {log_file}")
    return items


def crawl_article_pages(urls: list[str]) -> dict[str, ArticleExtractionResult]:
    crawled_pages = crawl_articles(urls)
    results: dict[str, ArticleExtractionResult] = {}

    for page in crawled_pages:
        request_url = str(page.get("request_url") or "").strip()
        page_url = str(page.get("url") or "").strip()
        if not request_url and not page_url:
            continue

        article_result = ArticleExtractionResult(
            url=page_url,
            title=str(page.get("title") or ""),
            text=str(page.get("text") or ""),
            published_at=str(page.get("published_at") or ""),
            success=bool(page.get("success")),
            error=str(page.get("error") or ""),
        )
        if request_url:
            results[request_url] = article_result
        if page_url and page_url != request_url:
            results[page_url] = article_result

    return results
