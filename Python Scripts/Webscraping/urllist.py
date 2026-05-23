"""Compatibility wrapper for ``market_data.urllist``.

Prefer importing from ``market_data.urllist`` in new code.
Keep this file as a backward-compatible shim only.
"""

from market_data.urllist import *  # noqa: F401,F403
