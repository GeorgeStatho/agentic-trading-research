"""Compatibility wrapper for ``agent_pipeline.main``.

Prefer importing from ``agent_pipeline.main`` in new code.
Keep this file as a backward-compatible shim only.
"""

from __future__ import annotations

import runpy

from _paths import add_agent_caller_paths


add_agent_caller_paths()

from agent_pipeline.main import *  # noqa: F401,F403


if __name__ == "__main__":
    runpy.run_module("agent_pipeline.main", run_name="__main__")
