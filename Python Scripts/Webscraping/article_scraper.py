"""Compatibility wrapper for ``engine.article_scraper``.

Prefer importing from ``engine.article_scraper`` in new code.
Keep this file as a backward-compatible shim only.
"""

from engine.article_scraper import (
    ArticleSpider,
    crawl_article_pages,
    crawl_articles,
    extract_links,
    extract_search_links,
)

