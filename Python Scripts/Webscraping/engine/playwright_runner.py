from __future__ import annotations

import os
from typing import Any

from article_extraction import DEFAULT_USER_AGENT
from core.scrape_logging import get_scrape_logger


LOGGER = get_scrape_logger("playwright_runner")
DEFAULT_PLAYWRIGHT_TIMEOUT_MS = 30_000
PLAYWRIGHT_BACKEND_NAME = "playwright"
PLAYWRIGHT_ARTICLE_DOMAINS = (
    "www.barrons.com",
    "barrons.com",
    "www.marketwatch.com",
    "marketwatch.com",
    "www.morningstar.com",
    "morningstar.com",
)


def get_article_crawl_backend() -> str:
    return os.getenv("WEBSCRAPING_ARTICLE_BACKEND", "scrapy").strip().lower() or "scrapy"


def should_use_playwright_backend() -> bool:
    return get_article_crawl_backend() == PLAYWRIGHT_BACKEND_NAME


def should_use_playwright_for_url(url: str) -> bool:
    lowered = (url or "").strip().lower()
    if not lowered:
        return False
    return any(domain in lowered for domain in PLAYWRIGHT_ARTICLE_DOMAINS)


def _get_sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright backend requested, but the Playwright Python package is not installed."
        ) from exc
    return sync_playwright


def fetch_rendered_pages(
    urls: list[str],
    *,
    timeout_ms: int = DEFAULT_PLAYWRIGHT_TIMEOUT_MS,
) -> list[dict[str, Any]]:
    if not urls:
        return []

    sync_playwright = _get_sync_playwright()
    rendered_pages: list[dict[str, Any]] = []
    LOGGER.info("Starting Playwright article-page crawl for %s URLs", len(urls))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=DEFAULT_USER_AGENT,
            locale="en-US",
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Upgrade-Insecure-Requests": "1",
            },
        )

        try:
            for url in urls:
                page = context.new_page()
                try:
                    LOGGER.info("Playwright requesting %s", url)
                    response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    try:
                        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5_000))
                    except Exception:
                        # Some sites never reach true network idle because of
                        # background requests. The DOM content is enough for our
                        # extractors, so we keep going.
                        pass

                    rendered_pages.append(
                        {
                            "request_url": url,
                            "url": page.url,
                            "html": page.content(),
                            "status": response.status if response else 200,
                            "error": "",
                        }
                    )
                except Exception as exc:
                    LOGGER.error("Playwright request failed for %s: %s", url, exc)
                    rendered_pages.append(
                        {
                            "request_url": url,
                            "url": url,
                            "html": "",
                            "status": None,
                            "error": str(exc),
                        }
                    )
                finally:
                    page.close()
        finally:
            context.close()
            browser.close()

    LOGGER.info("Finished Playwright article-page crawl for %s URLs", len(urls))
    return rendered_pages
