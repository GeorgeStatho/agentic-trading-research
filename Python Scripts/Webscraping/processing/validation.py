"""Content-quality checks applied before articles are persisted."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


DEFAULT_MIN_ARTICLE_BODY_CHARS = 200
BLOCK_PAGE_MARKERS = (
    "enable cookies",
    "accept cookies",
    "captcha",
    "access denied",
    "temporarily unavailable",
    "subscribe to continue",
    "sign in to continue",
    "verify you are human",
)


@dataclass(frozen=True, slots=True)
class ArticleValidation:
    is_valid: bool
    reason: str = ""


def minimum_article_body_chars() -> int:
    raw_value = os.getenv("WEBSCRAPING_MIN_ARTICLE_BODY_CHARS", str(DEFAULT_MIN_ARTICLE_BODY_CHARS))
    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_MIN_ARTICLE_BODY_CHARS


def validate_article_content(*, title: str, body: str, minimum_body_chars: int | None = None) -> ArticleValidation:
    """Reject empty, blocked, and implausibly short extraction results."""

    normalized_title = " ".join((title or "").split())
    normalized_body = re.sub(r"\s+", " ", body or "").strip()
    minimum = minimum_article_body_chars() if minimum_body_chars is None else max(1, minimum_body_chars)

    if not normalized_title:
        return ArticleValidation(False, "Article title is empty.")
    if len(normalized_body) < minimum:
        return ArticleValidation(False, f"Article body is shorter than {minimum} characters.")

    # A long, genuine article can discuss a CAPTCHA or subscription.  Treat a
    # marker as a blocked page only when the whole extraction is still short.
    if len(normalized_body) < max(minimum * 2, 500):
        content = f"{normalized_title} {normalized_body}".lower()
        for marker in BLOCK_PAGE_MARKERS:
            if marker in content:
                return ArticleValidation(False, f"Article appears to be a blocked or gated page ({marker}).")
    return ArticleValidation(True)
