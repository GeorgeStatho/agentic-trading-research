"""Compatibility wrapper for ``processing.news_normalization``.

Prefer importing from ``processing.news_normalization`` in new code.
Keep this file as a backward-compatible shim only.
"""

from processing.news_normalization import build_content_hash, normalize_title, normalize_url

