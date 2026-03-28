from __future__ import annotations

import json
import re
from html import unescape
from typing import Iterable
from urllib.parse import urlsplit

from scrapy.http import Response

from article_extraction import ArticleExtractionResult, clean_text


# These selectors are grounded in the saved CNBC HTML snapshots in:
# - `Stock-trading-experiment/html_pages/cnbc/search.htm`
# - `Stock-trading-experiment/html_pages/cnbc/Iran war_ Energy prices, material access threaten semiconductor demand.htm`
#
# The search page exposes Queryly templates for rendered result cards such as:
# - `.SearchResult-searchResult`
# - `.SearchResult-searchResultTitle`
# - `.SearchResult-searchResultPreview`
# - `.SearchResult-author`
# - `.SearchResult-publishedDate`
#
# The article page is richer and includes:
# - visible article header classes like `.ArticleHeader-headline`
# - structured page data in `window.__s_data`
# - body content under `page.page.layout[*].columns[*].modules[*].data.body.content`
SEARCH_CARD_SELECTORS = (
    ".SearchResult-searchResult",
    "div.SearchResult-searchResult",
)
SEARCH_LINK_SELECTORS = (
    ".SearchResult-searchResultTitle a.resultlink",
    ".SearchResult-searchResultTitle a",
    "a.resultlink",
)
SEARCH_TITLE_SELECTORS = (
    ".SearchResult-searchResultTitle .Card-title::text",
    ".SearchResult-searchResultTitle a::text",
    ".Card-title::text",
)
SEARCH_SUMMARY_SELECTORS = (
    ".SearchResult-searchResultPreview::text",
    ".SearchResult-searchResultPreview *::text",
)
SEARCH_AUTHOR_SELECTORS = (
    ".SearchResult-author::text",
    ".Card-byline::text",
)
SEARCH_TIMESTAMP_SELECTORS = (
    ".SearchResult-publishedDate::text",
    "time::attr(datetime)",
    "time::text",
)
ARTICLE_TITLE_SELECTORS = (
    ".ArticleHeader-headline::text",
    "meta[property='og:title']::attr(content)",
    "meta[name='twitter:title']::attr(content)",
    "title::text",
)
ARTICLE_TIMESTAMP_SELECTORS = (
    ".ArticleHeader-timeHidden time[itemprop='datePublished']::attr(datetime)",
    "time[data-testid='published-timestamp']::attr(datetime)",
    "meta[itemprop='datePublished']::attr(content)",
    "meta[name='datePublished']::attr(content)",
)
ARTICLE_BODY_SELECTORS = (
    ".group p::text",
    ".ArticleBody-articleBody p::text",
    "[data-module='ArticleBody'] p::text",
    "article p::text",
    "main p::text",
)
CNBC_ARTICLE_RE = re.compile(r"^https?://www\.cnbc\.com/\d{4}/\d{2}/\d{2}/[^?#]+\.html(?:[?#].*)?$", re.IGNORECASE)
CNBC_STATE_RE = re.compile(r"window\.__s_data\s*=\s*(\{.*?\})\s*;\s*window\.__c_data=", re.DOTALL)


def _first_text(response: Response, selectors: Iterable[str]) -> str:
    for selector in selectors:
        values = response.css(selector).getall()
        cleaned = " ".join(" ".join(value.split()) for value in values if value and value.strip()).strip()
        if cleaned:
            return cleaned
    return ""


def is_cnbc_url(url: str) -> bool:
    return "cnbc.com" in (url or "").lower()


def is_cnbc_article_url(url: str) -> bool:
    return bool(CNBC_ARTICLE_RE.match((url or "").strip()))


def response_looks_like_cnbc_search(response: Response) -> bool:
    if not is_cnbc_url(response.url):
        return False
    path = urlsplit(response.url).path.lower()
    return path == "/search/" or path == "/search"


def extract_cnbc_search_links(response: Response) -> list[dict]:
    results: list[dict] = []
    seen_hrefs: set[str] = set()

    for card_selector in SEARCH_CARD_SELECTORS:
        cards = response.css(card_selector)
        if not cards:
            continue

        for card in cards:
            href = ""
            for link_selector in SEARCH_LINK_SELECTORS:
                anchor = card.css(link_selector)
                href = anchor.attrib.get("href", "").strip() if anchor else ""
                if href:
                    break

            absolute_href = response.urljoin(href) if href else ""
            if not absolute_href or absolute_href in seen_hrefs or not is_cnbc_article_url(absolute_href):
                continue

            title = _first_text(card, SEARCH_TITLE_SELECTORS)
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

    # The saved search page is mostly a Queryly-rendered shell, so keep a URL
    # fallback for live pages where the rendered result cards are missing.
    for link in response.css("a[href]"):
        href = response.urljoin(link.attrib.get("href", "").strip())
        if not href or href in seen_hrefs or not is_cnbc_article_url(href):
            continue

        title = clean_text(link.css("::text").getall())
        if not title:
            parent = link.xpath("ancestor::*[contains(@class, 'SearchResult-searchResult')][1]")
            title = _first_text(parent, SEARCH_TITLE_SELECTORS) if parent else ""

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


def _extract_cnbc_body_children(children) -> list[str]:
    parts: list[str] = []
    if isinstance(children, str):
        if children.strip():
            parts.append(children)
        return parts

    if isinstance(children, list):
        for child in children:
            parts.extend(_extract_cnbc_body_children(child))
        return parts

    if isinstance(children, dict):
        nested_children = children.get("children")
        if nested_children is not None:
            parts.extend(_extract_cnbc_body_children(nested_children))
        return parts

    return parts


def _extract_cnbc_article_from_state(response: Response) -> ArticleExtractionResult | None:
    html = response.text
    match = CNBC_STATE_RE.search(html)
    if not match:
        return None

    try:
        state = json.loads(unescape(match.group(1)))
    except json.JSONDecodeError:
        return None

    layout = (
        state.get("page", {})
        .get("page", {})
        .get("layout", [])
    )

    article_data = None
    for row in layout:
        for column in row.get("columns", []):
            for module in column.get("modules", []):
                data = module.get("data")
                if not isinstance(data, dict):
                    continue
                body = data.get("body")
                headline = data.get("headline") or data.get("title")
                if body and headline:
                    article_data = data
                    break
            if article_data is not None:
                break
        if article_data is not None:
            break

    if article_data is None:
        return None

    title = str(article_data.get("headline") or article_data.get("title") or "").strip()
    published_at = str(article_data.get("datePublished") or article_data.get("dateLastPublished") or "").strip()

    body_parts: list[str] = []
    for block in article_data.get("body", {}).get("content", []):
        if not isinstance(block, dict):
            continue
        tag_name = block.get("tagName")
        if tag_name == "div":
            body_parts.extend(_extract_cnbc_body_children(block.get("children", [])))
        elif tag_name == "p":
            body_parts.extend(_extract_cnbc_body_children(block.get("children", [])))

    text = clean_text(body_parts)
    if text:
        return ArticleExtractionResult(
            url=response.url,
            title=title,
            text=text,
            published_at=published_at,
            success=True,
        )

    summary = str(article_data.get("summary") or article_data.get("description") or "").strip()
    if summary:
        return ArticleExtractionResult(
            url=response.url,
            title=title,
            text=summary,
            published_at=published_at,
            success=True,
        )

    return None


def extract_cnbc_article(response: Response) -> ArticleExtractionResult:
    state_result = _extract_cnbc_article_from_state(response)
    if state_result is not None:
        return state_result

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
        error="No CNBC article text found with the current selectors.",
    )
