from __future__ import annotations

import json
from html import unescape
from typing import Iterable
from urllib.parse import urlsplit

from scrapy.http import Response

from article_extraction import ArticleExtractionResult, clean_text


SEARCH_CARD_SELECTORS = (
    ".element.element--article",
    "div.element--article",
)
SEARCH_HEADLINE_SELECTORS = (
    ".article__headline a.link",
    ".article__headline a",
    "h3.article__headline a",
)
SEARCH_SUMMARY_SELECTORS = (
    ".article__summary *::text",
    ".article__summary::text",
)
SEARCH_TIMESTAMP_SELECTORS = (
    ".article__timestamp::attr(data-est)",
    ".article__timestamp::text",
)
SEARCH_AUTHOR_SELECTORS = (
    ".article__author::text",
    ".article__details .article__author::text",
)
ARTICLE_TITLE_SELECTORS = (
    "meta[property='og:title']::attr(content)",
    "meta[name='twitter:title']::attr(content)",
    "h1.article__headline::text",
    "h1.article-headline::text",
    "h1::text",
    "title::text",
)
ARTICLE_BODY_SELECTORS = (
    "#js-article__body p::text",
    ".article__body p::text",
    ".article-body p::text",
    "[data-testid='article-body'] p::text",
    "article .article__body p::text",
    "article p::text",
    "main p::text",
)
ARTICLE_TIMESTAMP_SELECTORS = (
    "meta[property='article:published_time']::attr(content)",
    "meta[name='article:published_time']::attr(content)",
    "meta[itemprop='datePublished']::attr(content)",
    "meta[name='dateLastPubbed']::attr(content)",
    ".article__timestamp::attr(data-est)",
    "time::attr(datetime)",
)
MARKETWATCH_ARTICLE_PATH_HINTS = (
    "/story/",
    "/amp/story/",
)


def _first_text(response: Response, selectors: Iterable[str]) -> str:
    for selector in selectors:
        values = response.css(selector).getall()
        cleaned = " ".join(" ".join(value.split()) for value in values if value and value.strip()).strip()
        if cleaned:
            return cleaned
    return ""


def _walk_nested_text(value) -> list[str]:
    parts: list[str] = []
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text)

        nested = value.get("nested")
        if isinstance(nested, list):
            for item in nested:
                parts.extend(_walk_nested_text(item))

        nested_text_and_decorations = value.get("nestedTextAndDecorations")
        if isinstance(nested_text_and_decorations, list):
            for item in nested_text_and_decorations:
                parts.extend(_walk_nested_text(item))

    elif isinstance(value, list):
        for item in value:
            parts.extend(_walk_nested_text(item))

    return parts


def _extract_marketwatch_article_from_next_data(response: Response) -> ArticleExtractionResult | None:
    script = response.css("script#__NEXT_DATA__::text").get()
    if not script:
        return None

    try:
        next_data = json.loads(unescape(script))
    except json.JSONDecodeError:
        return None

    article = next_data.get("props", {}).get("pageProps", {}).get("article", {})
    attributes = article.get("attributes", {})

    headline = attributes.get("headline") or article.get("headline")
    if isinstance(headline, dict):
        title = str(headline.get("text") or "").strip()
    else:
        title = str(headline or "").strip()

    published_at = (
        str(article.get("publishedDateTime") or "").strip()
        or str(attributes.get("publishedDateTime") or "").strip()
        or _first_text(response, ARTICLE_TIMESTAMP_SELECTORS)
    )

    body_parts: list[str] = []
    for block in attributes.get("articleBody") or []:
        if not isinstance(block, dict):
            continue
        text_and_decorations = block.get("textAndDecorations")
        if text_and_decorations:
            body_parts.extend(_walk_nested_text(text_and_decorations))

    text = clean_text(body_parts)
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

    return None


def is_marketwatch_url(url: str) -> bool:
    return "marketwatch.com" in (url or "").lower()


def is_marketwatch_article_url(url: str) -> bool:
    lowered = (url or "").lower()
    return is_marketwatch_url(lowered) and any(path_hint in lowered for path_hint in MARKETWATCH_ARTICLE_PATH_HINTS)


def extract_marketwatch_search_links(response: Response) -> list[dict]:
    results: list[dict] = []
    seen_hrefs: set[str] = set()

    for card_selector in SEARCH_CARD_SELECTORS:
        cards = response.css(card_selector)
        if not cards:
            continue

        for card in cards:
            href = ""
            title = ""
            for headline_selector in SEARCH_HEADLINE_SELECTORS:
                anchor = card.css(headline_selector)
                href = anchor.attrib.get("href", "").strip() if anchor else ""
                title = clean_text(anchor.css("::text").getall()) if anchor else ""
                if href:
                    break

            if not href:
                continue

            absolute_href = response.urljoin(href)
            if absolute_href in seen_hrefs or not is_marketwatch_article_url(absolute_href):
                continue

            summary = _first_text(card, SEARCH_SUMMARY_SELECTORS)
            published_at = _first_text(card, SEARCH_TIMESTAMP_SELECTORS)
            author = _first_text(card, SEARCH_AUTHOR_SELECTORS)
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

    for link in response.css("a[href*='/story/']"):
        href = response.urljoin(link.attrib.get("href", "").strip())
        if not href or href in seen_hrefs or not is_marketwatch_article_url(href):
            continue
        title = clean_text(link.css("::text").getall())
        if not title:
            continue
        results.append({"href": href, "text": title, "title": title, "summary": "", "author": "", "published_at": ""})
        seen_hrefs.add(href)

    return results


def extract_marketwatch_article(response: Response) -> ArticleExtractionResult:
    next_data_result = _extract_marketwatch_article_from_next_data(response)
    if next_data_result is not None:
        return next_data_result

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

    return ArticleExtractionResult(
        url=response.url,
        title=title,
        text="",
        published_at=published_at,
        success=False,
        error="No MarketWatch article text found with the current selectors.",
    )


def response_looks_like_marketwatch_search(response: Response) -> bool:
    if not is_marketwatch_url(response.url):
        return False
    path = urlsplit(response.url).path.lower()
    return path == "/search" or path.startswith("/search/")

