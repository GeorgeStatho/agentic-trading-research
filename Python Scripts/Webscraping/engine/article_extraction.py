from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import Iterable

import requests
import scrapy
from scrapy.http import HtmlResponse, Request, Response

from core.scrape_logging import get_scrape_logger


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)
LOGGER = get_scrape_logger("article_extraction")


@dataclass(slots=True)
class ArticleExtractionResult:
    url: str
    title: str = ""
    text: str = ""
    published_at: str = ""
    success: bool = False
    error: str = ""


@dataclass(slots=True)
class RenderedPageExtraction:
    request_url: str
    page_url: str
    status: int | None = None
    fetch_error: str = ""
    response: Response | None = None
    article: ArticleExtractionResult | None = None


def clean_text(parts: Iterable[str]) -> str:
    cleaned = []
    for part in parts:
        value = " ".join(part.split())
        if value:
            cleaned.append(value)
    return "\n".join(cleaned)


def _normalize_datetime(value: str | None) -> str:
    # Normalize the datetime so downstream code can rely on one consistent shape.
    if not value:
        return ""

    cleaned = value.strip()
    if not cleaned:
        return ""

    candidates = [
        cleaned,
        cleaned.replace("Z", "+00:00"),
    ]
    formats = [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]

    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass

        for fmt in formats:
            try:
                parsed = datetime.strptime(candidate, fmt)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc).isoformat()
            except ValueError:
                continue

    return ""


def _extract_published_at(response: Response) -> str:
    meta_selectors = [
        "meta[property='article:published_time']::attr(content)",
        "meta[name='article:published_time']::attr(content)",
        "meta[property='og:published_time']::attr(content)",
        "meta[name='og:published_time']::attr(content)",
        "meta[name='pubdate']::attr(content)",
        "meta[name='publish-date']::attr(content)",
        "meta[name='date']::attr(content)",
        "meta[itemprop='datePublished']::attr(content)",
        "time::attr(datetime)",
    ]

    for selector in meta_selectors:
        value = response.css(selector).get()
        normalized = _normalize_datetime(value)
        if normalized:
            return normalized

    return ""


def extract_from_response(response: Response) -> ArticleExtractionResult:
    # Extract the from response from the raw response and return a stable value.
    title = response.css("article h1::text, main h1::text, h1::text, title::text").get(default="").strip()
    published_at = _extract_published_at(response)

    paragraph_candidates = [
        "[data-article-body] p::text",
        "article p::text",
        "main p::text",
        "[role='main'] p::text",
        ".article p::text",
        ".post-content p::text",
        ".entry-content p::text",
        ".story-body p::text",
        ".content p::text",
        "body p::text",
    ]

    for selector in paragraph_candidates:
        paragraphs = response.css(selector).getall()
        text = clean_text(paragraphs)
        if text:
            return ArticleExtractionResult(
                url=response.url,
                title=title,
                text=text,
                published_at=published_at,
                success=True,
            )

    return ArticleExtractionResult(
        url=response.url,
        title=title,
        text="",
        published_at=published_at,
        success=False,
        error="No article text found with the public demo selectors.",
    )


def extract_rendered_page(rendered_page: dict) -> RenderedPageExtraction:
    # Extract the rendered page from the raw response and return a stable value.
    request_url = str(rendered_page.get("request_url") or "").strip()
    page_url = str(rendered_page.get("url") or request_url).strip()
    status = rendered_page.get("status")
    fetch_error = str(rendered_page.get("error") or "").strip()
    html = rendered_page.get("html") or ""

    if fetch_error or not html:
        return RenderedPageExtraction(
            request_url=request_url or page_url,
            page_url=page_url,
            status=status,
            fetch_error=fetch_error or "Playwright returned no HTML content.",
        )

    response = HtmlResponse(
        url=page_url,
        body=html.encode("utf-8"),
        encoding="utf-8",
        request=Request(url=request_url or page_url),
    )
    if status is not None:
        response = response.replace(status=int(status))

    return RenderedPageExtraction(
        request_url=request_url or page_url,
        page_url=page_url,
        status=response.status,
        fetch_error="",
        response=response,
        article=extract_from_response(response),
    )


def extract_rendered_pages_parallel(
    rendered_pages: list[dict],
    *,
    max_workers: int = 4,
) -> list[RenderedPageExtraction]:
    if not rendered_pages:
        return []

    worker_count = max(1, min(int(max_workers), len(rendered_pages)))
    if worker_count == 1:
        return [extract_rendered_page(rendered_page) for rendered_page in rendered_pages]

    LOGGER.info(
        "Extracting %s rendered pages in parallel with %s workers",
        len(rendered_pages),
        worker_count,
    )
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return list(executor.map(extract_rendered_page, rendered_pages))


def extract_article(url: str, timeout: int = 20) -> ArticleExtractionResult:
    # Extract the article from the raw response and return a stable value.
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://example.com/",
    }
    LOGGER.info("Fetching article URL %s", url)
    try:
        response = requests.get(url, timeout=timeout, headers=headers)
        response.raise_for_status()
    except requests.RequestException as exc:
        LOGGER.warning("Article fetch failed for %s: %s", url, exc)
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
    result = extract_from_response(scrapy_response)
    if result.success:
        LOGGER.info("Article extracted successfully for %s", result.url)
    else:
        LOGGER.warning("Article extraction failed for %s: %s", result.url, result.error)
    return result
