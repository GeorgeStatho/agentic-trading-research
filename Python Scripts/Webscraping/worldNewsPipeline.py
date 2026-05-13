"""Compatibility wrapper for ``pipelines.worldNewsPipeline``.

Prefer importing from ``pipelines.worldNewsPipeline`` in new code.
Keep this file as a backward-compatible shim only.
"""

from pipelines.worldNewsPipeline import *  # noqa: F401,F403
