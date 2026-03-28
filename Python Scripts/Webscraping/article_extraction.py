from __future__ import annotations

from dataclasses import dataclass
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
    success: bool = False
    error: str = ""


def clean_text(parts: Iterable[str]) -> str:
    cleaned = []
    for part in parts:
        value = " ".join(part.split())
        if value:
            cleaned.append(value)
    return "\n".join(cleaned)


def extract_from_response(response: Response) -> ArticleExtractionResult:
    title = response.css("title::text").get(default="").strip()

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
                success=True,
            )

    return ArticleExtractionResult(
        url=response.url,
        title=title,
        text="",
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
