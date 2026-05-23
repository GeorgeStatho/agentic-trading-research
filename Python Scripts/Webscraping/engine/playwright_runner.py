from __future__ import annotations

import asyncio
import os
from typing import Any
from engine.article_extraction import DEFAULT_USER_AGENT
from core.scrape_logging import get_scrape_logger


LOGGER = get_scrape_logger("playwright_runner")
DEFAULT_PLAYWRIGHT_TIMEOUT_MS = 30_000
DEFAULT_PLAYWRIGHT_CONCURRENCY = 5
PLAYWRIGHT_BACKEND_NAME = "playwright"
DEFAULT_PLAYWRIGHT_URL_HINTS = ("render=true", "/interactive/", "/dynamic/")


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
    return any(hint in lowered for hint in DEFAULT_PLAYWRIGHT_URL_HINTS)


def should_use_playwright_for_source_url(url: str) -> bool:
    return should_use_playwright_for_url(url)


async def _prepare_page_for_capture(page, url: str, timeout_ms: int) -> None:
    del url
    del timeout_ms
    try:
        await page.evaluate("window.scrollTo(0, Math.min(document.body.scrollHeight, 1200));")
    except Exception:
        pass


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
    # Fetch the single rendered page and normalize the result for the next stage.
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
    # Fetch the rendered pages async and normalize the result for the next stage.
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
