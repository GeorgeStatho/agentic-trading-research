"""Compatibility wrapper for ``pipelines.sectorNewsPipeline``.

Prefer importing from ``pipelines.sectorNewsPipeline`` in new code.
Keep this file as a backward-compatible shim only.
"""

from pipelines.sectorNewsPipeline import *  # noqa: F401,F403
