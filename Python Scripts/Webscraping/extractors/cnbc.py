from __future__ import annotations

import json
import re
from html import unescape
from typing import Any, Iterable
from urllib.parse import urlsplit

from scrapy.http import Response

from article_extraction import ArticleExtractionResult, clean_text
from core.scrape_logging import get_scrape_logger


SEARCH_CARD_SELECTORS = (
    ".SearchResult-searchResult",
    "div.SearchResult-searchResult",
    "article",
    "[data-testid*='search'] article",
    "[data-testid*='Search'] article",
    "li",
)
SEARCH_LINK_SELECTORS = (
    ".SearchResult-searchResultTitle a.resultlink",
    ".SearchResult-searchResultTitle a",
    "a.resultlink",
    "a[href*='/20']",
    "a[href*='.html']",
)
SEARCH_TITLE_SELECTORS = (
    ".SearchResult-searchResultTitle .Card-title::text",
    ".SearchResult-searchResultTitle a::text",
    ".Card-title::text",
    "h1 a::text",
    "h2 a::text",
    "h3 a::text",
    "h4 a::text",
    "h1::text",
    "h2::text",
    "h3::text",
    "h4::text",
    "a[href*='.html'] *::text",
)
SEARCH_SUMMARY_SELECTORS = (
    ".SearchResult-searchResultPreview::text",
    ".SearchResult-searchResultPreview *::text",
    "p::text",
    "div[class*='description']::text",
    "div[class*='summary']::text",
)
SEARCH_AUTHOR_SELECTORS = (
    ".SearchResult-author::text",
    ".Card-byline::text",
    "[class*='author']::text",
    "[class*='byline']::text",
)
SEARCH_TIMESTAMP_SELECTORS = (
    ".SearchResult-publishedDate::text",
    "time::attr(datetime)",
    "time::text",
    "[class*='date']::text",
    "[class*='timestamp']::text",
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
CNBC_ARTICLE_RE = re.compile(
    r"^https?://www\.cnbc\.com/\d{4}/\d{2}/\d{2}/[^?#]+\.html(?:[?#].*)?$",
    re.IGNORECASE,
)
CNBC_STATE_RE = re.compile(r"window\.__s_data\s*=\s*(\{.*?\})\s*;\s*window\.__c_data=", re.DOTALL)
CNBC_ARTICLE_URL_IN_HTML_RE = re.compile(
    r"https?://www\.cnbc\.com/\d{4}/\d{2}/\d{2}/[^\"'\\<>\s]+?\.html(?:\?[^\"'\\<>\s]*)?",
    re.IGNORECASE,
)
LOGGER = get_scrape_logger("cnbc_extractor")


def _first_text(response: Response, selectors: Iterable[str]) -> str:
    for selector in selectors:
        values = response.css(selector).getall()
        cleaned = " ".join(" ".join(value.split()) for value in values if value and value.strip()).strip()
        if cleaned:
            return cleaned
    return ""


def _clean_json_text(value: str) -> str:
    return value.lstrip("\ufeff").strip()


def _maybe_json_load(text: str) -> Any | None:
    cleaned = _clean_json_text(text)
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def _iter_json_candidates(payload: Any):
    if isinstance(payload, dict):
        yield payload
        for value in payload.values():
            yield from _iter_json_candidates(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_json_candidates(item)


def _pick_text(*values: Any) -> str:
    for value in values:
        cleaned = " ".join(str(value or "").split()).strip()
        if cleaned:
            return cleaned
    return ""


def _append_cnbc_search_result(
    results: list[dict],
    seen_hrefs: set[str],
    *,
    href: str,
    title: str = "",
    summary: str = "",
    author: str = "",
    published_at: str = "",
) -> None:
    absolute_href = href.strip()
    if not absolute_href:
        return
    if absolute_href in seen_hrefs or not is_cnbc_article_url(absolute_href):
        return

    cleaned_title = _pick_text(title)
    cleaned_summary = _pick_text(summary)
    cleaned_author = _pick_text(author)
    cleaned_published_at = _pick_text(published_at)
    result_text = "\n".join(
        value
        for value in (
            cleaned_title,
            cleaned_summary,
            cleaned_author,
            cleaned_published_at,
        )
        if value
    )

    results.append(
        {
            "href": absolute_href,
            "text": result_text or cleaned_title or absolute_href,
            "title": cleaned_title,
            "summary": cleaned_summary,
            "author": cleaned_author,
            "published_at": cleaned_published_at,
        }
    )
    seen_hrefs.add(absolute_href)


def _extract_cnbc_search_links_from_html(response: Response) -> list[dict]:
    results: list[dict] = []
    seen_hrefs: set[str] = set()

    for match in CNBC_ARTICLE_URL_IN_HTML_RE.finditer(response.text):
        href = response.urljoin(match.group(0))
        _append_cnbc_search_result(
            results,
            seen_hrefs,
            href=href,
            title="",
            summary="",
            author="",
            published_at="",
        )

    return results


def _extract_cnbc_search_links_from_ld_json(response: Response) -> list[dict]:
    results: list[dict] = []
    seen_hrefs: set[str] = set()

    for raw_script in response.css("script[type='application/ld+json']::text").getall():
        payload = _maybe_json_load(raw_script)
        if payload is None:
            continue

        for node in _iter_json_candidates(payload):
            item_list = node.get("itemListElement") if isinstance(node, dict) else None
            if not isinstance(item_list, list):
                continue

            for item in item_list:
                if not isinstance(item, dict):
                    continue

                nested_item = item.get("item")
                nested_item = nested_item if isinstance(nested_item, dict) else {}
                href = _pick_text(
                    item.get("url"),
                    nested_item.get("url"),
                    nested_item.get("@id"),
                )
                if href:
                    href = response.urljoin(href)

                _append_cnbc_search_result(
                    results,
                    seen_hrefs,
                    href=href,
                    title=_pick_text(
                        item.get("name"),
                        nested_item.get("name"),
                        nested_item.get("headline"),
                    ),
                    summary=_pick_text(
                        item.get("description"),
                        nested_item.get("description"),
                    ),
                    author=_pick_text(
                        item.get("author"),
                        nested_item.get("author"),
                    ),
                    published_at=_pick_text(
                        item.get("datePublished"),
                        nested_item.get("datePublished"),
                    ),
                )

    return results


def _extract_cnbc_search_links_from_json_scripts(response: Response) -> list[dict]:
    results: list[dict] = []
    seen_hrefs: set[str] = set()

    for raw_script in response.css("script::text").getall():
        payload = _maybe_json_load(raw_script)
        if payload is None:
            continue

        for node in _iter_json_candidates(payload):
            if not isinstance(node, dict):
                continue

            href = _pick_text(
                node.get("url"),
                node.get("canonicalUrl"),
                node.get("shareUrl"),
            )
            if href:
                href = response.urljoin(href)

            title = _pick_text(
                node.get("headline"),
                node.get("title"),
                node.get("name"),
            )
            if not href or not title:
                continue

            _append_cnbc_search_result(
                results,
                seen_hrefs,
                href=href,
                title=title,
                summary=_pick_text(node.get("description"), node.get("summary")),
                author=_pick_text(node.get("author"), node.get("creator")),
                published_at=_pick_text(node.get("datePublished"), node.get("publishedAt")),
            )

    return results


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
    debug_resultlink_hrefs = [
        response.urljoin(str(href).strip())
        for href in response.css("a.resultlink::attr(href)").getall()
        if str(href).strip()
    ]
    debug_card_titles = [
        " ".join(text.split())
        for text in response.css("a.resultlink .Card-title::text").getall()
        if str(text).strip()
    ]

    structured_results = _extract_cnbc_search_links_from_ld_json(response)
    for result in structured_results:
        href = str(result.get("href") or "").strip()
        _append_cnbc_search_result(
            results,
            seen_hrefs,
            href=href,
            title=str(result.get("title") or ""),
            summary=str(result.get("summary") or ""),
            author=str(result.get("author") or ""),
            published_at=str(result.get("published_at") or ""),
        )

    json_script_results = _extract_cnbc_search_links_from_json_scripts(response)
    for result in json_script_results:
        href = str(result.get("href") or "").strip()
        _append_cnbc_search_result(
            results,
            seen_hrefs,
            href=href,
            title=str(result.get("title") or ""),
            summary=str(result.get("summary") or ""),
            author=str(result.get("author") or ""),
            published_at=str(result.get("published_at") or ""),
        )

    dom_result_count_before = len(results)
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
            _append_cnbc_search_result(
                results,
                seen_hrefs,
                href=absolute_href,
                title=_first_text(card, SEARCH_TITLE_SELECTORS),
                summary=_first_text(card, SEARCH_SUMMARY_SELECTORS),
                author=_first_text(card, SEARCH_AUTHOR_SELECTORS),
                published_at=_first_text(card, SEARCH_TIMESTAMP_SELECTORS),
            )
    dom_result_count = len(results) - dom_result_count_before

    anchor_result_count_before = len(results)

    for link in response.css("a[href]"):
        href = response.urljoin(link.attrib.get("href", "").strip())
        title = clean_text(link.css("::text").getall())
        if not title:
            parent = link.xpath("ancestor::article[1] | ancestor::li[1] | ancestor::section[1] | ancestor::div[1]")
            title = _first_text(parent, SEARCH_TITLE_SELECTORS) if parent else ""

        parent = link.xpath("ancestor::article[1] | ancestor::li[1] | ancestor::section[1] | ancestor::div[1]")
        summary = _first_text(parent, SEARCH_SUMMARY_SELECTORS) if parent else ""
        author = _first_text(parent, SEARCH_AUTHOR_SELECTORS) if parent else ""
        published_at = _first_text(parent, SEARCH_TIMESTAMP_SELECTORS) if parent else ""

        _append_cnbc_search_result(
            results,
            seen_hrefs,
            href=href,
            title=title,
            summary=summary,
            author=author,
            published_at=published_at,
        )
    anchor_result_count = len(results) - anchor_result_count_before

    html_scan_results = _extract_cnbc_search_links_from_html(response)
    for result in html_scan_results:
        href = str(result.get("href") or "").strip()
        _append_cnbc_search_result(
            results,
            seen_hrefs,
            href=href,
            title=str(result.get("title") or ""),
            summary=str(result.get("summary") or ""),
            author=str(result.get("author") or ""),
            published_at=str(result.get("published_at") or ""),
        )

    LOGGER.info(
        "Raw extracted CNBC links count for %s: total=%s (ld_json=%s, json_scripts=%s, dom=%s, anchor=%s, html_scan=%s)",
        response.url,
        len(results),
        len(structured_results),
        len(json_script_results),
        dom_result_count,
        anchor_result_count,
        len(html_scan_results),
    )
    LOGGER.info(
        "CNBC search debug for %s: resultlink_count=%s sample_resultlink_hrefs=%s sample_card_titles=%s",
        response.url,
        len(debug_resultlink_hrefs),
        debug_resultlink_hrefs[:5],
        debug_card_titles[:5],
    )

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

    layout = state.get("page", {}).get("page", {}).get("layout", [])

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

