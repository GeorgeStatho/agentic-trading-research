"""Compatibility wrapper for ``pipelines.companyNewsPipeline``.

Prefer importing from ``pipelines.companyNewsPipeline`` in new code.
Keep this file as a backward-compatible shim only.
"""

from pipelines.companyNewsPipeline import *  # noqa: F401,F403


if __name__ == "__main__":
    get_all_company_news()
