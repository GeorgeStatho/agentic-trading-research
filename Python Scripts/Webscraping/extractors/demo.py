from __future__ import annotations

from typing import Iterable

from scrapy.http import Response

from engine.article_extraction import ArticleExtractionResult, clean_text


DEMO_DOMAIN_HINTS = ("example.com", "example.org", "quotes.toscrape.com")
DEMO_ARTICLE_PATH_HINTS = ("/demo/articles/", "/demo/posts/", "/post/", "/article/", "/story/")


def _first_text(response: Response, selectors: Iterable[str]) -> str:
    for selector in selectors:
        values = [value.strip() for value in response.css(selector).getall() if value.strip()]
        if values:
            return " ".join(values)
    return ""


def is_demo_url(url: str) -> bool:
    lowered = (url or "").strip().lower()
    return any(domain in lowered for domain in DEMO_DOMAIN_HINTS)


def is_demo_article_url(url: str) -> bool:
    lowered = (url or "").strip().lower()
    return is_demo_url(lowered) and any(hint in lowered for hint in DEMO_ARTICLE_PATH_HINTS)


def response_looks_like_demo_search(response: Response) -> bool:
    lowered = response.url.lower()
    if "search" in lowered or "?q=" in lowered:
        return True
    return bool(response.css("[data-demo-search-result], .search-results, .results"))


def extract_demo_search_links(response: Response) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()

    for link in response.css("a"):
        href = str(link.css("::attr(href)").get() or "").strip()
        if not href:
            continue
        absolute_href = response.urljoin(href)
        if absolute_href in seen or not is_demo_article_url(absolute_href):
            continue
        text = " ".join(part.strip() for part in link.css("::text").getall() if part.strip())
        links.append({"href": absolute_href, "text": text})
        seen.add(absolute_href)

    return links


def extract_demo_article(response: Response) -> ArticleExtractionResult:
    title = _first_text(response, ("article h1::text", "main h1::text", "h1::text", "title::text"))
    published_at = _first_text(
        response,
        (
            "meta[property='article:published_time']::attr(content)",
            "meta[name='pubdate']::attr(content)",
            "time::attr(datetime)",
        ),
    )

    for selector in (
        "[data-article-body] p::text",
        "article p::text",
        "main p::text",
        ".post-content p::text",
        ".entry-content p::text",
        "body p::text",
    ):
        text = clean_text(response.css(selector).getall())
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
        error="No article text found in the demo extractor.",
    )
