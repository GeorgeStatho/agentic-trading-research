"""Compatibility wrapper for ``engine.article_extraction``.

Prefer importing from ``engine.article_extraction`` in new code.
Keep this file as a backward-compatible shim only.
"""

from engine.article_extraction import (
    ArticleExtractionResult,
    DEFAULT_USER_AGENT,
    RenderedPageExtraction,
    clean_text,
    extract_article,
    extract_from_response,
    extract_rendered_page,
    extract_rendered_pages_parallel,
)

