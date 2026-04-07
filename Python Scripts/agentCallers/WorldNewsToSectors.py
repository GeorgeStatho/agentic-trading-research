from __future__ import annotations

import runpy

from _paths import add_agent_caller_paths


add_agent_caller_paths()

from agent_stages.world_news_to_sectors import *  # noqa: F401,F403


if __name__ == "__main__":
    runpy.run_module("agent_stages.world_news_to_sectors", run_name="__main__")
