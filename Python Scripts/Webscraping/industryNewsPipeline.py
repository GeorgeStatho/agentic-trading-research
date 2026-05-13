"""Compatibility wrapper for ``pipelines.industryNewsPipeline``.

Prefer importing from ``pipelines.industryNewsPipeline`` in new code.
Keep this file as a backward-compatible shim only.
"""

from pipelines.industryNewsPipeline import *  # noqa: F401,F403


if __name__ == "__main__":
    get_all_industry_news()
