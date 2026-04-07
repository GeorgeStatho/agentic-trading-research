from __future__ import annotations

import runpy

from _paths import add_agent_caller_paths


add_agent_caller_paths()

from agent_analysis.sector_interest import *  # noqa: F401,F403


if __name__ == "__main__":
    runpy.run_module("agent_analysis.sector_interest", run_name="__main__")
