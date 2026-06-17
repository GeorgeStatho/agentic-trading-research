"""Compatibility exports for manager context helpers.

Older imports and a few targeted tests still reach through `manager_context`,
so this module stays intentionally thin while the split implementation lives in
more focused modules.
"""

from .manager_payload_context import (
    _build_context_snapshot,
    _build_manager_visible_market_context,
    _build_manager_visible_supporting_articles,
    _build_manager_visible_views_summary,
    payload_has_evidence,
)
from .manager_strategist_context import (
    _build_manager_visible_strategist_context,
    _normalize_strategist_recommendation,
)
