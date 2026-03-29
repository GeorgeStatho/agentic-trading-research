from __future__ import annotations

import json
import re
from typing import Iterable
from urllib.parse import urlsplit

from scrapy.http import Response

from article_extraction import ArticleExtractionResult, clean_text


# The saved Fool quote-page HTML shows two useful patterns:
# 1. JSON-LD under `#quote-page-related-schema` that already contains related
#    article URLs and headlines.
# 2. Visible related/news cards rendered as `article` blocks with links to
#    `/investing/...` article paths.
#
# That means the coverage/news area appears to be scrapeable even if the live
# site exposes it behind a `Coverage` tab in the UI.
ARTICLE_TITLE_SELECTORS = (
    "meta[property='og:title']::attr(content)",
    "meta[name='twitter:title']::attr(content)",
    "h1::text",
    "title::text",
)
ARTICLE_BODY_SELECTORS = (
    "article p::text",
    "main p::text",
    "[role='main'] p::text",
    ".article-content p::text",
    ".content-block p::text",
    "body p::text",
)
ARTICLE_TIMESTAMP_SELECTORS = (
    "meta[property='article:published_time']::attr(content)",
    "meta[name='article:published_time']::attr(content)",
    "meta[itemprop='datePublished']::attr(content)",
    "time::attr(datetime)",
)
FOOL_ARTICLE_RE = re.compile(r"^https?://(www\.)?fool\.com/(investing|retirement|crypto|research|markets)/[^?#]+/?$", re.IGNORECASE)
FOOL_QUOTE_PATH_RE = re.compile(r"^/quote/[^/]+/[^/]+/?$", re.IGNORECASE)


def _first_text(response: Response, selectors: Iterable[str]) -> str:
    for selector in selectors:
        values = response.css(selector).getall()
        cleaned = " ".join(" ".join(value.split()) for value in values if value and value.strip()).strip()
        if cleaned:
            return cleaned
    return ""


def is_fool_url(url: str) -> bool:
    return "fool.com" in (url or "").lower()


def is_fool_quote_url(url: str) -> bool:
    if not is_fool_url(url):
        return False
    path = urlsplit(url).path
    return bool(FOOL_QUOTE_PATH_RE.match(path))


def is_fool_article_url(url: str) -> bool:
    return bool(FOOL_ARTICLE_RE.match((url or "").strip()))


def response_looks_like_fool_quote(response: Response) -> bool:
    return is_fool_quote_url(response.url)


def _extract_fool_links_from_json_ld(response: Response) -> list[dict]:
    results: list[dict] = []
    seen_hrefs: set[str] = set()

    for script_text in response.css("script[type='application/ld+json']::text").getall():
        try:
            payload = json.loads(script_text)
        except json.JSONDecodeError:
            continue

        payloads = payload if isinstance(payload, list) else [payload]
        for item in payloads:
            if not isinstance(item, dict):
                continue
            if item.get("@type") != "ItemList":
                continue

            for article in item.get("itemListElement", []):
                if not isinstance(article, dict):
                    continue
                href = response.urljoin(str(article.get("url") or "").strip())
                if not href or href in seen_hrefs or not is_fool_article_url(href):
                    continue
                title = str(article.get("headline") or "").strip()
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


def extract_fool_quote_links(response: Response) -> list[dict]:
    json_ld_results = _extract_fool_links_from_json_ld(response)
    if json_ld_results:
        return json_ld_results

    results: list[dict] = []
    seen_hrefs: set[str] = set()

    for article in response.css("article"):
        link = article.css("a[href]")
        href = response.urljoin(link.attrib.get("href", "").strip()) if link else ""
        if not href or href in seen_hrefs or not is_fool_article_url(href):
            continue

        title = clean_text(article.css("h3::text").getall()) or clean_text(link.css("::text").getall())
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

    if results:
        return results

    for link in response.css("a[href]"):
        href = response.urljoin(link.attrib.get("href", "").strip())
        if not href or href in seen_hrefs or not is_fool_article_url(href):
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


def extract_fool_article(response: Response) -> ArticleExtractionResult:
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

    description = _first_text(
        response,
        (
            "meta[name='description']::attr(content)",
            "meta[property='og:description']::attr(content)",
            "meta[name='twitter:description']::attr(content)",
        ),
    )
    if description:
        return ArticleExtractionResult(
            url=response.url,
            title=title,
            text=description,
            published_at=published_at,
            success=True,
        )

    return ArticleExtractionResult(
        url=response.url,
        title=title,
        text="",
        published_at=published_at,
        success=False,
        error="No Motley Fool article text found with the current selectors.",
    )
