from __future__ import annotations

import json
import re
from typing import Iterable

from scrapy.http import Response

from engine.article_extraction import ArticleExtractionResult, clean_text


ARTICLE_TITLE_SELECTORS = (
    "meta[property='og:title']::attr(content)",
    "meta[name='twitter:title']::attr(content)",
    "meta[name='title']::attr(content)",
    "h1::text",
    "title::text",
)
ARTICLE_TIMESTAMP_SELECTORS = (
    "meta[property='article:published_time']::attr(content)",
    "meta[name='article:published_time']::attr(content)",
    "meta[itemprop='datePublished']::attr(content)",
    "meta[name='datePublished']::attr(content)",
    "time::attr(datetime)",
    "time::text",
)
# Paragraph elements, not ::text: p::text drops the text inside links (tickers, company names).
ARTICLE_BODY_SELECTORS = (
    ".bodyItems-wrapper p",  # the article body; "article p" also catches the Coinbase tooltip above it
    "[data-testid='article-content-wrapper'] p",
    "[data-testid='article-content'] p",
    ".caas-body p",
    ".caas-content p",
    ".article-wrap p",
    ".article-body p",
    ".article-content p",
    "[class*='ArticleContent'] p",
    "article p",
    "main p",
)
ARTICLE_SUMMARY_SELECTORS = (
    "meta[name='description']::attr(content)",
    "meta[property='og:description']::attr(content)",
    "meta[name='twitter:description']::attr(content)",
)
YAHOO_ARTICLE_RE = re.compile(
    r"^https?://finance\.yahoo\.com/(news|markets/[^/]+/articles)/[^?#]+(?:[?#].*)?$",
    re.IGNORECASE,
)


def _first_text(response: Response, selectors: Iterable[str]) -> str:
    for selector in selectors:
        values = response.css(selector).getall()
        cleaned = " ".join(" ".join(value.split()) for value in values if value and value.strip()).strip()
        if cleaned:
            return cleaned
    return ""


def _extract_yahoo_article_schema(response: Response) -> dict | None:
    for script_text in response.css("script[type='application/ld+json']::text").getall():
        if not script_text or "NewsArticle" not in script_text:
            continue
        try:
            payload = json.loads(script_text)
        except json.JSONDecodeError:
            continue

        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            schema_type = str(candidate.get("@type") or "").strip()
            if schema_type in {"NewsArticle", "Article"}:
                return candidate
    return None


def is_yahoo_url(url: str) -> bool:
    return "finance.yahoo.com" in (url or "").lower()


def is_yahoo_article_url(url: str) -> bool:
    return bool(YAHOO_ARTICLE_RE.match((url or "").strip()))


def extract_yahoo_article(response: Response) -> ArticleExtractionResult:
    # Extract the yahoo article from the raw response and return a stable value.
    schema = _extract_yahoo_article_schema(response) or {}

    title = str(schema.get("headline") or "").strip() or _first_text(response, ARTICLE_TITLE_SELECTORS)
    published_at = str(schema.get("datePublished") or "").strip() or _first_text(response, ARTICLE_TIMESTAMP_SELECTORS)

    for selector in ARTICLE_BODY_SELECTORS:
        text = clean_text(paragraph.xpath("string(.)").get() for paragraph in response.css(selector))
        if text:
            return ArticleExtractionResult(
                url=response.url,
                title=title,
                text=text,
                published_at=published_at,
                success=True,
            )

    summary = str(schema.get("description") or "").strip() or _first_text(response, ARTICLE_SUMMARY_SELECTORS)
    if summary:
        return ArticleExtractionResult(
            url=response.url,
            title=title,
            text=summary,
            published_at=published_at,
            success=True,
        )

    return ArticleExtractionResult(
        url=response.url,
        title=title,
        text="",
        published_at=published_at,
        success=False,
        error="No Yahoo Finance article text found with the current selectors.",
    )
