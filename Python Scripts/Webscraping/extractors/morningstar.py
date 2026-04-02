from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urljoin
from urllib.parse import urlsplit

from scrapy.http import Response

from article_extraction import ArticleExtractionResult, clean_text


SEARCH_CARD_SELECTORS = (
    ".mdc-feed__mdc > .mdc-feed-section__mdc > .mdc-feed-section__contents__mdc > a.mdc-basic-feed-item__mdc",
    ".mdc-feed-section__contents__mdc > a.mdc-basic-feed-item__mdc",
    ".mdc-feed__mdc a.mdc-basic-feed-item__mdc.mdc-basic-feed-item--block-link__mdc",
    "a.mdc-basic-feed-item__mdc.mdc-basic-feed-item--block-link__mdc",
    "a.mdc-basic-feed-item__mdc",
)
SEARCH_LINK_SELECTORS = (
    "::attr(href)",
    "::attr(to)",
    "::attr(data-href)",
    "::attr(data-url)",
    "::attr(data-link)",
    "::attr(itemid)",
    "a.mdc-basic-feed-item__mdc::attr(href)",
    "a.mdc-basic-feed-item__mdc::attr(to)",
    "a.mdc-link--block__mdc::attr(href)",
    "a.mdc-link--block__mdc::attr(to)",
    "header h3 a::attr(href)",
    "a::attr(href)",
)
SEARCH_RESULT_PATH_RE = re.compile(
    r'(?:"|\\u002F)(/((stocks|markets|news|financial-advisors|personal-finance|retirement|funds|etfs)/[^"\\s<,)]*))',
    re.IGNORECASE,
)
SEARCH_TITLE_SELECTORS = (
    ".mdc-basic-feed-item__title__mdc *::text",
    "header h3 *::text",
)
SEARCH_SUMMARY_SELECTORS = (
    ".mdc-basic-feed-item__body__mdc *::text",
    ".mdc-basic-feed-item__body__mdc::text",
)
SEARCH_AUTHOR_SELECTORS = (
    ".mdc-metadata__list__mdc span::text",
    ".story__byline-text__mdc::text",
)
SEARCH_TIMESTAMP_SELECTORS = (
    ".mdc-metadata__list__mdc time::attr(datetime)",
    ".mdc-metadata__list__mdc time::text",
    "time::attr(datetime)",
    "time::text",
)
ARTICLE_TITLE_SELECTORS = (
    "meta[property='og:title']::attr(content)",
    "meta[name='twitter:title']::attr(content)",
    "h1.story__headline__mdc::text",
    "h1[itemprop='headline']::text",
    "h1::text",
    "title::text",
)
ARTICLE_TIMESTAMP_SELECTORS = (
    "meta[property='og:article:published_time']::attr(content)",
    "meta[name='parsely-pub-date']::attr(content)",
    "meta[itemprop='datePublished']::attr(content)",
    "time[itemprop='datePublished']::attr(datetime)",
    "time::attr(datetime)",
    "time::text",
)
ARTICLE_BODY_SELECTORS = (
    ".story__body__mdc p::text",
    ".story__body__mdc *::text",
    ".mdc-story-body__mdc p::text",
    ".mdc-story-body__mdc *::text",
    "[itemprop='articleBody'] p::text",
    "[itemprop='articleBody'] *::text",
    "article[itemtype*='NewsArticle'] p::text",
    "article[itemtype*='NewsArticle'] *::text",
    "article p::text",
    "article div::text",
    "main article *::text",
    "main p::text",
    "main div::text",
)
ARTICLE_SUMMARY_SELECTORS = (
    "p.story__subtitle__mdc::text",
    "meta[name='description']::attr(content)",
    "meta[property='og:description']::attr(content)",
)
MORNINGSTAR_ARTICLE_RE = re.compile(
    r"^https?://(www\.)?morningstar\.com/(stocks|markets|news|financial-advisors|personal-finance|retirement|funds|etfs)/[^?#]+$",
    re.IGNORECASE,
)


def _first_text(response: Response, selectors: Iterable[str]) -> str:
    for selector in selectors:
        values = response.css(selector).getall()
        cleaned = " ".join(" ".join(value.split()) for value in values if value and value.strip()).strip()
        if cleaned:
            return cleaned
    return ""


def _extract_card_href(card: Response, page_url: str) -> str:
    for link_selector in SEARCH_LINK_SELECTORS:
        href = (card.css(link_selector).get() or "").strip()
        if href:
            return urljoin(page_url, href)

    for attr_name in ("href", "to", "data-href", "data-url", "data-link", "itemid"):
        href = (card.attrib.get(attr_name) or "").strip()
        if href:
            return urljoin(page_url, href)

    return ""


def is_morningstar_url(url: str) -> bool:
    return "morningstar.com" in (url or "").lower()


def is_morningstar_article_url(url: str) -> bool:
    return bool(MORNINGSTAR_ARTICLE_RE.match((url or "").strip()))


def response_looks_like_morningstar_search(response: Response) -> bool:
    if not is_morningstar_url(response.url):
        return False
    path = urlsplit(response.url).path.lower()
    return path == "/search" or path == "/search/"


def extract_morningstar_search_links(response: Response) -> list[dict]:
    results: list[dict] = []
    seen_hrefs: set[str] = set()

    for card_selector in SEARCH_CARD_SELECTORS:
        cards = response.css(card_selector)
        if not cards:
            continue

        for card in cards:
            absolute_href = _extract_card_href(card, response.url)
            if not absolute_href or absolute_href in seen_hrefs or not is_morningstar_article_url(absolute_href):
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

    # Morningstar sometimes renders article paths into the page payload even if
    # the clickable feed wrapper is not easy to target through CSS. Use the
    # raw HTML as a final fallback so search pages still yield article URLs.
    page_text = response.text or ""
    for match in SEARCH_RESULT_PATH_RE.finditer(page_text):
        relative_href = (match.group(1) or "").replace("\\u002F", "/").strip()
        href = urljoin(response.url, relative_href)
        if not href or href in seen_hrefs or not is_morningstar_article_url(href):
            continue

        results.append(
            {
                "href": href,
                "text": href,
                "title": "",
                "summary": "",
                "author": "",
                "published_at": "",
            }
        )
        seen_hrefs.add(href)

    if results:
        return results

    for link in response.css("a[href]"):
        href = response.urljoin((link.attrib.get("href") or "").strip())
        if not href or href in seen_hrefs or not is_morningstar_article_url(href):
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


def extract_morningstar_article(response: Response) -> ArticleExtractionResult:
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
        error="No Morningstar article text found with the current selectors.",
    )
