"""Compatibility wrapper for ``pipelines.USNewsPipeline``.

Prefer importing from ``pipelines.USNewsPipeline`` in new code.
Keep this file as a backward-compatible shim only.
"""

from pipelines.USNewsPipeline import *  # noqa: F401,F403
