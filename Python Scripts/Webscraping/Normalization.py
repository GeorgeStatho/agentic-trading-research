"""Compatibility wrapper for ``engine.Normalization``.

Prefer importing from ``engine.Normalization`` in new code.
Keep this file as a backward-compatible shim only.
"""

from engine.Normalization import (
    ArticleSpider,
    ArticleExtractionResult,
    DEFAULT_USER_AGENT,
    crawl_article_pages,
    crawl_articles,
    extract_article,
    extract_from_response,
)
