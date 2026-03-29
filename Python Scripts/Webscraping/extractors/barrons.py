from __future__ import annotations

import json
import re
from html import unescape
from typing import Iterable
from urllib.parse import urlsplit

from scrapy.http import Response

from article_extraction import ArticleExtractionResult, clean_text


SEARCH_CARD_SELECTORS = (
    "article.BarronsTheme--search--gc1tZfSe",
    "article[class*='BarronsTheme--story']",
)
SEARCH_LINK_SELECTORS = (
    "a.BarronsTheme--headline-link--2s0JerNw",
    "h4 a",
    "a[href*='/articles/']",
)
SEARCH_TITLE_SELECTORS = (
    ".BarronsTheme--headline--1Q8XnyIf *::text",
    "h4 *::text",
)
SEARCH_SUMMARY_SELECTORS = (
    ".BarronsTheme--summary--3UHA7uDx *::text",
    "p[class*='summary'] *::text",
)
SEARCH_AUTHOR_SELECTORS = (
    ".BarronsTheme--byline--3BdHtEoV::text",
    "p[class*='byline']::text",
)
SEARCH_TIMESTAMP_SELECTORS = (
    ".BarronsTheme--timestamp--3V5jp2-R::text",
    "p[class*='timestamp']::text",
    "time::text",
)
ARTICLE_TITLE_SELECTORS = (
    "meta[property='og:title']::attr(content)",
    "meta[name='twitter:title']::attr(content)",
    "meta[name='article.headline']::attr(content)",
    "h1::text",
    "title::text",
)
ARTICLE_TIMESTAMP_SELECTORS = (
    "meta[property='article:published_time']::attr(content)",
    "meta[name='article:published_time']::attr(content)",
    "meta[itemprop='datePublished']::attr(content)",
    "meta[name='article.published']::attr(content)",
    "time::attr(datetime)",
    "time::text",
)
ARTICLE_BODY_SELECTORS = (
    "article p::text",
    "main p::text",
    "[data-module='ArticleBody'] p::text",
    "[class*='article'] p::text",
    "[class*='body'] p::text",
)
ARTICLE_SUMMARY_SELECTORS = (
    "meta[name='description']::attr(content)",
    "meta[property='og:description']::attr(content)",
    "meta[name='twitter:description']::attr(content)",
)
BARRONS_ARTICLE_RE = re.compile(
    r"^https?://www\.barrons\.com/articles/[^?#]+(?:[?#].*)?$",
    re.IGNORECASE,
)


def _first_text(response: Response, selectors: Iterable[str]) -> str:
    for selector in selectors:
        values = response.css(selector).getall()
        cleaned = " ".join(" ".join(value.split()) for value in values if value and value.strip()).strip()
        if cleaned:
            return cleaned
    return ""


def is_barrons_url(url: str) -> bool:
    return "barrons.com" in (url or "").lower()


def is_barrons_article_url(url: str) -> bool:
    return bool(BARRONS_ARTICLE_RE.match((url or "").strip()))


def response_looks_like_barrons_search(response: Response) -> bool:
    if not is_barrons_url(response.url):
        return False

    path = urlsplit(response.url).path.lower()
    title = response.css("title::text").get(default="").strip().lower()
    return path == "/search" or "search | barron" in title


def barrons_response_is_blocked(response: Response) -> bool:
    html = response.text.lower()
    return (
        "please enable js and disable any ad blocker" in html
        or "captcha-delivery.com" in html
        or "geo.captcha-delivery.com" in html
    )


def extract_barrons_search_links(response: Response) -> list[dict]:
    results: list[dict] = []
    seen_hrefs: set[str] = set()

    for card_selector in SEARCH_CARD_SELECTORS:
        cards = response.css(card_selector)
        if not cards:
            continue

        for card in cards:
            href = ""
            title = ""
            for link_selector in SEARCH_LINK_SELECTORS:
                anchor = card.css(link_selector)
                href = anchor.attrib.get("href", "").strip() if anchor else ""
                title = clean_text(anchor.css("::text").getall()) if anchor else ""
                if href:
                    break

            absolute_href = response.urljoin(href) if href else ""
            if not absolute_href or absolute_href in seen_hrefs or not is_barrons_article_url(absolute_href):
                continue

            summary = _first_text(card, SEARCH_SUMMARY_SELECTORS)
            author = _first_text(card, SEARCH_AUTHOR_SELECTORS)
            published_at = _first_text(card, SEARCH_TIMESTAMP_SELECTORS)
            result_text = "\n".join(value for value in (title, summary, author, published_at) if value)

            results.append(
                {
                    "href": absolute_href,
                    "text": result_text or title or absolute_href,
                    "title": title,
                    "summary": summary,
                    "author": author,
                    "published_at": published_at,
                }
            )
            seen_hrefs.add(absolute_href)

        if results:
            return results

    for link in response.css("a[href*='/articles/']"):
        href = response.urljoin(link.attrib.get("href", "").strip())
        if not href or href in seen_hrefs or not is_barrons_article_url(href):
            continue

        title = clean_text(link.css("::text").getall())
        if not title:
            continue

        results.append(
            {
                "href": href,
                "text": title,
                "title": title,
                "summary": "",
                "author": "",
                "published_at": "",
            }
        )
        seen_hrefs.add(href)

    return results


def _extract_barrons_article_from_json_ld(response: Response) -> ArticleExtractionResult | None:
    for script_text in response.css("script[type='application/ld+json']::text").getall():
        try:
            payload = json.loads(unescape(script_text))
        except json.JSONDecodeError:
            continue

        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue

            item_type = str(item.get("@type") or "").lower()
            if item_type not in {"newsarticle", "article", "reportagenewsarticle"}:
                continue

            title = str(item.get("headline") or "").strip()
            published_at = str(item.get("datePublished") or "").strip()
            text = str(item.get("articleBody") or item.get("description") or "").strip()
            if not text:
                continue

            return ArticleExtractionResult(
                url=response.url,
                title=title,
                text=text,
                published_at=published_at,
                success=True,
            )

    return None


def extract_barrons_article(response: Response) -> ArticleExtractionResult:
    if barrons_response_is_blocked(response):
        return ArticleExtractionResult(
            url=response.url,
            title=response.css("title::text").get(default="").strip(),
            text="",
            published_at="",
            success=False,
            error="Barron's returned an anti-bot or JavaScript interstitial instead of the article HTML.",
        )

    json_ld_result = _extract_barrons_article_from_json_ld(response)
    if json_ld_result is not None:
        return json_ld_result

    title = _first_text(response, ARTICLE_TITLE_SELECTORS)
    published_at = _first_text(response, ARTICLE_TIMESTAMP_SELECTORS)

    for selector in ARTICLE_BODY_SELECTORS:
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

    summary = _first_text(response, ARTICLE_SUMMARY_SELECTORS)
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
        error="No Barron's article text found with the current selectors.",
    )
