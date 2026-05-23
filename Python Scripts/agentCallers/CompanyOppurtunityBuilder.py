"""Compatibility wrapper for ``agent_builders.company_opportunity``.

Prefer importing from ``agent_builders.company_opportunity`` in new code.
Keep this file as a backward-compatible shim only.
"""

from __future__ import annotations

import runpy

from _paths import add_agent_caller_paths


add_agent_caller_paths()

from agent_builders.company_opportunity import *  # noqa: F401,F403


if __name__ == "__main__":
    runpy.run_module("agent_builders.company_opportunity", run_name="__main__")
