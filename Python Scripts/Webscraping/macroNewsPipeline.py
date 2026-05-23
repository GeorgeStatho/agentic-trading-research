import json

"""Compatibility wrapper for ``pipelines.macroNewsPipeline``.

Prefer importing from ``pipelines.macroNewsPipeline`` in new code.
Keep this file as a backward-compatible shim only.
"""

from pipelines.macroNewsPipeline import *  # noqa: F401,F403


if __name__ == "__main__":
    summary = ingest_macro_and_news()
    print(json.dumps(summary, indent=2, sort_keys=True))
