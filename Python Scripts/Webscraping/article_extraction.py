from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import Iterable

import requests
import scrapy
from scrapy.http import Response


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
    published_at: str = ""
    success: bool = False
    error: str = ""


def clean_text(parts: Iterable[str]) -> str:
    cleaned = []
    for part in parts:
        value = " ".join(part.split())
        if value:
            cleaned.append(value)
    return "\n".join(cleaned)


def _normalize_datetime(value: str | None) -> str:
    if not value:
        return ""

    cleaned = value.strip()
    if not cleaned:
        return ""

    # Common article date formats
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
    title = response.css("title::text").get(default="").strip()
    published_at = _extract_published_at(response)

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
        error="No article text found with the current selectors.",
    )


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
    return extract_from_response(scrapy_response)
