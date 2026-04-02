from __future__ import annotations

import asyncio
import os
from typing import Any
from article_extraction import DEFAULT_USER_AGENT
from core.scrape_logging import get_scrape_logger


LOGGER = get_scrape_logger("playwright_runner")
DEFAULT_PLAYWRIGHT_TIMEOUT_MS = 30_000
DEFAULT_PLAYWRIGHT_CONCURRENCY = 3
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


def _use_headed_browser(urls: list[str]) -> bool:
    return False


def _get_playwright_concurrency() -> int:
    raw_value = os.getenv("WEBSCRAPING_PLAYWRIGHT_CONCURRENCY", str(DEFAULT_PLAYWRIGHT_CONCURRENCY))
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_PLAYWRIGHT_CONCURRENCY
    return max(1, parsed)


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


async def _prepare_page_for_capture(page, url: str, timeout_ms: int) -> None:
    if not _is_cnbc_search_url(url):
        return

    # CNBC search results often arrive after the initial DOMContentLoaded event.
    # Wait for likely result selectors, then do a small scroll to trigger any
    # lazy-rendered cards before capturing page HTML.
    for selector in CNBC_SEARCH_WAIT_SELECTORS:
        try:
            await page.wait_for_selector(selector, timeout=min(timeout_ms, 8_000))
            break
        except Exception:
            continue

    try:
        await page.evaluate("window.scrollTo(0, Math.min(document.body.scrollHeight, 1200));")
    except Exception:
        pass


async def _log_cnbc_search_debug(page, url: str) -> None:
    if not _is_cnbc_search_url(url):
        return

    selector_counts: dict[str, int | str] = {}
    for selector in ("a.resultlink", ".SearchResult-searchResult", ".Card-title", "iframe"):
        try:
            selector_counts[selector] = await page.locator(selector).count()
        except Exception as exc:
            selector_counts[selector] = f"error: {exc}"

    sample_resultlinks: list[str] = []
    try:
        hrefs = await page.locator("a.resultlink").evaluate_all(
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


def _get_async_playwright():
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright backend requested, but the Playwright Python package is not installed."
        ) from exc
    return async_playwright


async def _fetch_single_rendered_page(context, url: str, timeout_ms: int) -> dict[str, Any]:
    page = await context.new_page()
    try:
        LOGGER.info("Playwright requesting %s", url)
        response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5_000))
        except Exception:
            # Some sites never reach true network idle because of
            # background requests. The DOM content is enough for our
            # extractors, so we keep going.
            pass

        await _prepare_page_for_capture(page, url, timeout_ms)
        await _log_cnbc_search_debug(page, url)

        return {
            "request_url": url,
            "url": page.url,
            "html": await page.content(),
            "status": response.status if response else 200,
            "error": "",
        }
    except Exception as exc:
        LOGGER.error("Playwright request failed for %s: %s", url, exc)
        return {
            "request_url": url,
            "url": url,
            "html": "",
            "status": None,
            "error": str(exc),
        }
    finally:
        await page.close()


async def _fetch_rendered_pages_async(
    urls: list[str],
    *,
    timeout_ms: int,
) -> list[dict[str, Any]]:
    if not urls:
        return []

    async_playwright = _get_async_playwright()
    headed_mode = _use_headed_browser(urls)
    concurrency = _get_playwright_concurrency()
    LOGGER.info("Starting Playwright article-page crawl for %s URLs", len(urls))
    LOGGER.info(
        "Playwright browser mode for this crawl: %s",
        "headed" if headed_mode else "headless",
    )
    LOGGER.info("Playwright concurrency for this crawl: %s", concurrency)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not headed_mode)
        context = await browser.new_context(
            user_agent=DEFAULT_USER_AGENT,
            locale="en-US",
        )

        semaphore = asyncio.Semaphore(concurrency)

        async def fetch_with_limit(url: str) -> dict[str, Any]:
            async with semaphore:
                return await _fetch_single_rendered_page(context, url, timeout_ms)

        try:
            rendered_pages = await asyncio.gather(*(fetch_with_limit(url) for url in urls))
        finally:
            await context.close()
            await browser.close()

    LOGGER.info("Finished Playwright article-page crawl for %s URLs", len(urls))
    return list(rendered_pages)


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def fetch_rendered_pages(
    urls: list[str],
    *,
    timeout_ms: int = DEFAULT_PLAYWRIGHT_TIMEOUT_MS,
) -> list[dict[str, Any]]:
    if not urls:
        return []
    return _run_async(_fetch_rendered_pages_async(urls, timeout_ms=timeout_ms))
