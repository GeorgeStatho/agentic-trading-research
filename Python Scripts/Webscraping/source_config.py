"""Compatibility wrapper for ``sources.source_config``.

Prefer importing from ``sources.source_config`` in new code.
Keep this file as a backward-compatible shim only.
"""

from sources.source_config import (
    get_article_patterns,
    get_max_article_age_days,
    get_source_metadata,
    is_allowed_source,
    supports_source_type,
)

