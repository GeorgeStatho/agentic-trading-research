from __future__ import annotations

import os
from typing import Any
from urllib.parse import parse_qs, urlsplit

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
PLAYWRIGHT_SOURCE_DOMAINS = (
    "www.cnbc.com",
    "cnbc.com",
)
CNBC_SEARCH_WAIT_SELECTORS = (
    "a.resultlink",
    ".SearchResult-searchResult",
    ".Card-title",
)
CNBC_SEARCH_INPUT_SELECTORS = (
    "input[type='search']",
    "input[placeholder*='Search']",
    "input[aria-label*='Search']",
    "input[name='query']",
)


def _use_headed_browser(urls: list[str]) -> bool:
    return False


def get_article_crawl_backend() -> str:
    return os.getenv("WEBSCRAPING_ARTICLE_BACKEND", "scrapy").strip().lower() or "scrapy"


def should_use_playwright_backend() -> bool:
    return get_article_crawl_backend() == PLAYWRIGHT_BACKEND_NAME


def should_use_playwright_for_url(url: str) -> bool:
    lowered = (url or "").strip().lower()
    if not lowered:
        return False
    return any(domain in lowered for domain in PLAYWRIGHT_ARTICLE_DOMAINS)


def should_use_playwright_for_source_url(url: str) -> bool:
    lowered = (url or "").strip().lower()
    if not lowered:
        return False
    if not any(domain in lowered for domain in PLAYWRIGHT_SOURCE_DOMAINS):
        return False
    return "/search" in lowered


def _is_cnbc_search_url(url: str) -> bool:
    lowered = (url or "").strip().lower()
    return "cnbc.com" in lowered and "/search" in lowered


def _prepare_page_for_capture(page, url: str, timeout_ms: int) -> None:
    if not _is_cnbc_search_url(url):
        return

    _prime_cnbc_search_query(page, url, timeout_ms)

    # CNBC search results often arrive after the initial DOMContentLoaded event.
    # Wait for likely result selectors, then do a small scroll to trigger any
    # lazy-rendered cards before capturing page HTML.
    for selector in CNBC_SEARCH_WAIT_SELECTORS:
        try:
            page.wait_for_selector(selector, timeout=min(timeout_ms, 8_000))
            break
        except Exception:
            continue

    try:
        page.evaluate("window.scrollTo(0, Math.min(document.body.scrollHeight, 1200));")
        page.wait_for_timeout(750)
    except Exception:
        pass


def _extract_cnbc_query_text(url: str) -> str:
    parsed = urlsplit(url)
    query_value = parse_qs(parsed.query).get("query", [""])[0]
    return " ".join(str(query_value or "").split()).strip()


def _prime_cnbc_search_query(page, url: str, timeout_ms: int) -> None:
    query_text = _extract_cnbc_query_text(url)
    if not query_text:
        return

    for selector in CNBC_SEARCH_INPUT_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            current_value = (locator.input_value(timeout=1_000) or "").strip()
            if current_value == query_text:
                return
            if current_value:
                LOGGER.info(
                    "CNBC search input for %s already has a different value: %s",
                    url,
                    current_value,
                )
            else:
                LOGGER.info(
                    "CNBC search input for %s was blank; filling query text: %s",
                    url,
                    query_text,
                )
            locator.click(timeout=2_000)
            locator.fill(query_text, timeout=min(timeout_ms, 5_000))
            locator.press("Enter", timeout=2_000)
            page.wait_for_timeout(1_000)
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5_000))
            except Exception:
                pass
            return
        except Exception:
            continue


def _log_cnbc_search_debug(page, url: str) -> None:
    if not _is_cnbc_search_url(url):
        return

    selector_counts: dict[str, int | str] = {}
    for selector in ("a.resultlink", ".SearchResult-searchResult", ".Card-title", "iframe"):
        try:
            selector_counts[selector] = page.locator(selector).count()
        except Exception as exc:
            selector_counts[selector] = f"error: {exc}"

    sample_resultlinks: list[str] = []
    try:
        hrefs = page.locator("a.resultlink").evaluate_all(
            """elements => elements
                .map(el => el.getAttribute('href') || '')
                .filter(Boolean)
                .slice(0, 5)"""
        )
        sample_resultlinks = [str(href).strip() for href in hrefs if str(href).strip()]
    except Exception as exc:
        sample_resultlinks = [f"error: {exc}"]

    frame_summaries: list[dict[str, str]] = []
    try:
        for frame in page.frames[:5]:
            frame_summaries.append(
                {
                    "url": str(frame.url or ""),
                    "name": str(frame.name or ""),
                }
            )
    except Exception as exc:
        frame_summaries = [{"error": str(exc)}]

    LOGGER.info(
        "CNBC Playwright live DOM debug for %s: selector_counts=%s sample_resultlinks=%s frames=%s",
        url,
        selector_counts,
        sample_resultlinks,
        frame_summaries,
    )


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
    headed_mode = _use_headed_browser(urls)
    LOGGER.info(
        "Playwright browser mode for this crawl: %s",
        "headed" if headed_mode else "headless",
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed_mode)
        context = browser.new_context(
            user_agent=DEFAULT_USER_AGENT,
            locale="en-US",
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

                    _prepare_page_for_capture(page, url, timeout_ms)
                    _log_cnbc_search_debug(page, url)

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
