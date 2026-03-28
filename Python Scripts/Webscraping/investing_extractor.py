from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urlsplit

from scrapy.http import Response

from article_extraction import ArticleExtractionResult, clean_text


# These selectors are grounded in the saved Investing.com HTML snapshots in:
# - `Stock-trading-experiment/html_pages/investing/Search.htm`
# - `Stock-trading-experiment/html_pages/investing/GlobalFoundries files patent infringement lawsuits against Tower Semiconductor By Reuters.htm`
#
# The search page exposes clear result cards under:
# - `.searchSectionMain`
# - `.largeTitle`
# - `.articleItem`
# - `.textDiv a.title`
# - `.articleDetails`
# - `time.date`
#
# The article page uses a dedicated article container under `#article`, with
# the body content rendered inside classes such as:
# - `.article_WYSIWYG__O0uhw`
# - `.article_articlePage__UMz3q`
# and standard meta tags for title/description/canonical URL.
SEARCH_SECTION_SELECTORS = (
    ".searchSectionMain",
    "div.searchSectionMain",
)
SEARCH_CARD_SELECTORS = (
    ".articleItem",
    "article.articleItem",
)
SEARCH_LINK_SELECTORS = (
    ".textDiv a.title",
    "a.title",
)
SEARCH_SUMMARY_SELECTORS = (
    ".textDiv p::text",
    "p.js-news-item-content::text",
)
SEARCH_AUTHOR_SELECTORS = (
    ".articleDetails span::text",
)
SEARCH_TIMESTAMP_SELECTORS = (
    ".articleDetails time.date::text",
    "time.date::text",
)
ARTICLE_TITLE_SELECTORS = (
    "meta[property='og:title']::attr(content)",
    "meta[name='twitter:title']::attr(content)",
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
ARTICLE_BODY_SELECTORS = (
    "#article .article_WYSIWYG__O0uhw p::text",
    "#article .article_articlePage__UMz3q p::text",
    "#article article p::text",
    "#article p::text",
    "article p::text",
    "main p::text",
)
ARTICLE_SUMMARY_SELECTORS = (
    "meta[name='description']::attr(content)",
    "meta[property='og:description']::attr(content)",
    "meta[name='twitter:description']::attr(content)",
)
INVESTING_ARTICLE_RE = re.compile(
    r"^https?://(www\.)?investing\.com/(news|analysis)/[^?#]+$",
    re.IGNORECASE,
)


def _first_text(response: Response, selectors: Iterable[str]) -> str:
    for selector in selectors:
        values = response.css(selector).getall()
        cleaned = " ".join(" ".join(value.split()) for value in values if value and value.strip()).strip()
        if cleaned:
            return cleaned
    return ""


def is_investing_url(url: str) -> bool:
    return "investing.com" in (url or "").lower()


def is_investing_article_url(url: str) -> bool:
    return bool(INVESTING_ARTICLE_RE.match((url or "").strip()))


def response_looks_like_investing_search(response: Response) -> bool:
    if not is_investing_url(response.url):
        return False
    path = urlsplit(response.url).path.lower()
    return path == "/search/" or path == "/search"


def extract_investing_search_links(response: Response) -> list[dict]:
    results: list[dict] = []
    seen_hrefs: set[str] = set()

    for section_selector in SEARCH_SECTION_SELECTORS:
        sections = response.css(section_selector)
        if not sections:
            continue

        for section in sections:
            header = clean_text(section.css("h2.groupHeader::text").getall()).lower()
            if header not in {"news", "analysis"}:
                continue

            for card_selector in SEARCH_CARD_SELECTORS:
                cards = section.css(card_selector)
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
                    if not absolute_href or absolute_href in seen_hrefs or not is_investing_article_url(absolute_href):
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

    # Keep a URL fallback for live pages that render fewer cards server-side
    # than the saved snapshot, while still restricting to clear news/analysis URLs.
    for link in response.css("a[href]"):
        href = response.urljoin(link.attrib.get("href", "").strip())
        if not href or href in seen_hrefs or not is_investing_article_url(href):
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


def extract_investing_article(response: Response) -> ArticleExtractionResult:
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
        error="No Investing.com article text found with the current selectors.",
    )
