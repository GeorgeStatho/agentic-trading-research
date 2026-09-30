from __future__ import annotations

import json
import logging

from services.runtime_paths import DATA_DIR
from services.settings import load_process_settings

if __name__ == "__main__":
    load_process_settings()


from agent_pipeline.cleanup import clear_current_pipeline_targets
from agent_pipeline.existing_db_view import run_agent_pipeline_from_existing_data
from agent_pipeline.news_refresh import run_agent_pipeline, run_news_collection_pipeline
from agent_pipeline.ranking import get_current_rankings
from agent_pipeline.target_selection import (
    build_company_opportunist_summary,
    collect_ranked_companies_for_industry,
    get_current_pipeline_targets,
)

__all__ = [
    "collect_ranked_companies_for_industry",
    "build_company_opportunist_summary",
    "clear_current_pipeline_targets",
    "get_current_pipeline_targets",
    "get_current_rankings",
    "run_agent_pipeline_from_existing_data",
    "run_news_collection_pipeline",
    "run_agent_pipeline",
]


def _configure_console_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
        force=True,
    )
if __name__ == "__main__":
    _configure_console_logging()
    clear_current_pipeline_targets()
    result = run_news_collection_pipeline()
    print(json.dumps(result, ensure_ascii=True, indent=2))

    output_path = DATA_DIR / "agent_pipeline_output.json"
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=True, indent=2)

    print(f"Saved agent pipeline output to {output_path}")
