from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

AGENT_STAGES_DIR = Path(__file__).resolve().parent
AGENT_CALLERS_DIR = AGENT_STAGES_DIR.parent
PYTHON_SCRIPTS_DIR = AGENT_CALLERS_DIR.parent
ROOT_DIR = PYTHON_SCRIPTS_DIR.parent
DATA_DIR = ROOT_DIR / "Data"
for path in (AGENT_CALLERS_DIR, PYTHON_SCRIPTS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.append(normalized)

from MacroNewsToSectors import (
    DEFAULT_CONTEXT_LIMIT,
    DEFAULT_MAX_ARTICLE_AGE_DAYS,
    DEFAULT_MODEL,
    DEFAULT_PROMPT_OVERHEAD_TOKENS,
    OLLAMA_HOST,
    Client,
    ask_model as _ask_model,
    build_macro_news_to_sectors_prompt,
    classify_macro_news_to_sectors,
    _configure_console_logging,
    get_recent_macro_news_articles,
    get_sector_reference,
    macro_news_classifier,
)


world_news_classifier = macro_news_classifier


def ask_model(client: Client, model: str, system_prompt: str, user_prompt: str) -> str:
    return _ask_model(client, model, system_prompt, user_prompt)


def get_recent_world_news_articles(
    max_age_days: int = DEFAULT_MAX_ARTICLE_AGE_DAYS,
) -> list[dict[str, Any]]:
    return get_recent_macro_news_articles("world", max_age_days=max_age_days)


def build_world_news_to_sectors_prompt(
    articles: list[dict[str, Any]],
    sectors: list[dict[str, Any]],
) -> tuple[str, str]:
    return build_macro_news_to_sectors_prompt(articles, sectors, news_scope="world")


def classify_world_news_to_sectors(
    *,
    client: Client = world_news_classifier,
    model: str = DEFAULT_MODEL,
    max_age_days: int = DEFAULT_MAX_ARTICLE_AGE_DAYS,
    context_limit: int = DEFAULT_CONTEXT_LIMIT,
    prompt_overhead_tokens: int = DEFAULT_PROMPT_OVERHEAD_TOKENS,
) -> list[dict[str, Any]]:
    return classify_macro_news_to_sectors(
        news_scope="world",
        client=client,
        model=model,
        max_age_days=max_age_days,
        context_limit=context_limit,
        prompt_overhead_tokens=prompt_overhead_tokens,
    )


if __name__ == "__main__":
    _configure_console_logging()
    pairs = classify_world_news_to_sectors(max_age_days=5)
    print(json.dumps(pairs, indent=2))
