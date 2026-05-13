"""Compatibility wrapper for ``agent_helpers.macro_news``.

Prefer importing from ``agent_helpers.macro_news`` in new code.
Keep this file as a backward-compatible shim only.
"""

from __future__ import annotations

import runpy

from _paths import add_agent_caller_paths


add_agent_caller_paths()

from agent_helpers.macro_news import *  # noqa: F401,F403


if __name__ == "__main__":
    runpy.run_module("agent_helpers.macro_news", run_name="__main__")
