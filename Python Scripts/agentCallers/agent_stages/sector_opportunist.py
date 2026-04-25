from __future__ import annotations

from datetime import datetime
import json
import logging
import os
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

from _sector_opportunist_helpers import (
    build_empty_sector_result,
    build_sector_opportunist_articles,
    build_sector_valid_reference_sets,
    extract_sector_impacts,
    get_sector_reference,
    normalize_sector_impact,
    save_sector_opportunist_batch_results,
)
from agent_helpers.opportunist_support import build_shared_opportunist_impacts_schema
from _shared import (
    Client,
    ask_llm_model,
    build_token_limited_batches,
    extract_json_value,
    get_model_client,
)


OLLAMA_HOST = os.getenv(
    "SECTOR_OPPURUNTIST_OLLAMA_HOST",
    os.getenv("MACRO_NEWS_OLLAMA_HOST", os.getenv("WORLD_NEWS_OLLAMA_HOST", "http://127.0.0.1:11434")),
)
DEFAULT_MODEL = os.getenv(
    "SECTOR_OPPURUNTIST_MODEL",
    os.getenv("MACRO_NEWS_MODEL", os.getenv("WORLD_NEWS_MODEL", "world-news-sectors")),
)

DEFAULT_MAX_ARTICLE_AGE_DAYS = 5
DEFAULT_CONTEXT_LIMIT = 4096
DEFAULT_PROMPT_OVERHEAD_TOKENS = 1200

_sector_opportunist_client: Client | None = None
LOGGER = logging.getLogger(__name__)
SECTOR_IMPACTS_SCHEMA: dict[str, Any] = build_shared_opportunist_impacts_schema()

__all__ = [
    "build_sector_opportunist_articles",
    "build_sector_opportunist_prompt",
    "classify_sector_articles",
    "get_sector_reference",
]


def _get_default_client() -> Client:
    global _sector_opportunist_client
    if _sector_opportunist_client is None:
        _sector_opportunist_client = get_model_client(OLLAMA_HOST)
    return _sector_opportunist_client


def ask_model(client: Client, model: str, system_prompt: str, user_prompt: str) -> str:
    return ask_llm_model(
        client,
        model,
        system_prompt,
        user_prompt,
        temperature=0,
        host_label=OLLAMA_HOST,
        response_schema=SECTOR_IMPACTS_SCHEMA,
    )


def build_sector_opportunist_prompt(
    sector: dict[str, Any],
    articles: list[dict[str, Any]],
    *,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> tuple[str, str]:
    default_system_prompt = (
        "You are a market analyst that maps sector-level news to likely impact for one sector. "
        "Return only valid JSON with a top-level key named 'impacts'. "
        "Do not include markdown fences, notes, or extra keys. "
        "Each item in 'impacts' must contain: confidence, impact_direction, impact_magnitude, materiality, "
        "time_horizon, effect_type, and reason. "
        "Do not include article_id. Do not include sector_id. Do not include sector_name. "
        "confidence must be one of: high, medium, low. "
        "impact_direction must be one of: positive, negative, neutral, mixed. "
        "impact_magnitude must be one of: major, moderate, modest, minimal. "
        "materiality must be one of: high, medium, low. "
        "impact_magnitude = size of the likely effect if the thesis is real. "
        "materiality = how important or relevant the article is for this sector. "
        "time_horizon must be one of: immediate, short_term, medium_term, unclear. "
        "effect_type must be one of: direct, indirect. "
        "Return an empty impacts array if the article is not materially relevant. "
        "Use neutral or mixed when the impact is unclear or two-sided. "
        "Reason must explain the causal chain. "
        "Do not classify sentiment alone; focus on likely business or investor impact. "
        "Prefer omission over weak guesses. "
        "Only include the supplied sector."
    )
    system_prompt = str(system_prompt_override or default_system_prompt)

    payload = {
        "task": str(task_override or "Map each sector-linked article to the likely impact on the supplied sector."),
        "sector": sector,
        "articles": [
            {
                "article_id": article["article_id"],
                "article_scope": article["article_scope"],
                "title": article["title"],
                "summary": article["summary"],
                "body": article["body"] or article["summary"],
                "published_at": article["published_at"],
                "source": article["source"],
                "source_url": article["source_url"],
                "macro_context": article.get("macro_context", {}),
                "sector_context": article.get("sector_context", {}),
            }
            for article in articles
        ],
        "required_output": {
            "impacts": [
                {
                    "confidence": "high|medium|low",
                    "impact_direction": "positive|negative|neutral|mixed",
                    "impact_magnitude": "major|moderate|modest|minimal",
                    "materiality": "high|medium|low",
                    "time_horizon": "immediate|short_term|medium_term|unclear",
                    "effect_type": "direct|indirect",
                    "reason": "short causal explanation",
                }
            ]
        },
    }
    return system_prompt, json.dumps(payload, ensure_ascii=True)


def _classify_article_batch(
    article_batch: list[dict[str, Any]],
    *,
    sector: dict[str, Any],
    client: Client,
    model: str,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    system_prompt, user_prompt = build_sector_opportunist_prompt(
        sector,
        article_batch,
        system_prompt_override=system_prompt_override,
        task_override=task_override,
    )
    raw_response = ask_model(
        client=client,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    parsed = extract_json_value(raw_response)
    impacts = extract_sector_impacts(parsed)
    return raw_response, impacts if isinstance(impacts, list) else []


def _collect_cleaned_impacts(
    article_batches: list[list[dict[str, Any]]],
    *,
    sector: dict[str, Any],
    client: Client,
    model: str,
    valid_article_ids: set[int],
    valid_sector_id: int,
    valid_sector_name: str,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> list[dict[str, Any]]:
    cleaned_impacts: list[dict[str, Any]] = []
    seen_impacts: set[tuple[int, int]] = set()

    for article_batch in article_batches:
        for article in article_batch:
            single_article_batch = [article]
            raw_response, batch_impacts = _classify_article_batch(
                single_article_batch,
                sector=sector,
                client=client,
                model=model,
                system_prompt_override=system_prompt_override,
                task_override=task_override,
            )
            batch_cleaned_impacts: list[dict[str, Any]] = []

            for impact in batch_impacts:
                if not isinstance(impact, dict):
                    continue

                normalized_impact = normalize_sector_impact(
                    impact,
                    source_article_id=int(article["article_id"]),
                    valid_sector_id=valid_sector_id,
                    valid_sector_name=valid_sector_name,
                )
                if normalized_impact is None:
                    continue

                dedupe_key = (
                    normalized_impact["article_id"],
                    normalized_impact["sector_id"],
                )
                if dedupe_key in seen_impacts:
                    continue

                cleaned_impacts.append(normalized_impact)
                batch_cleaned_impacts.append(normalized_impact)
                seen_impacts.add(dedupe_key)

            if not batch_cleaned_impacts:
                LOGGER.warning(
                    "No valid sector impacts were extracted for sector %s from article %s. Raw model response: %s",
                    sector["sector_key"],
                    int(article["article_id"]),
                    raw_response,
                )

            save_sector_opportunist_batch_results(
                single_article_batch,
                batch_cleaned_impacts,
                model=model,
                raw_response=raw_response,
            )

    return cleaned_impacts


def classify_sector_articles(
    sector_identifier: str,
    *,
    client: Client | None = None,
    model: str = DEFAULT_MODEL,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
    context_limit: int = DEFAULT_CONTEXT_LIMIT,
    prompt_overhead_tokens: int = DEFAULT_PROMPT_OVERHEAD_TOKENS,
) -> dict[str, Any]:
    client = client or _get_default_client()
    sector, articles = build_sector_opportunist_articles(
        sector_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )

    if not articles:
        return build_empty_sector_result(sector)

    valid_sector_id, valid_sector_name, valid_article_ids = build_sector_valid_reference_sets(
        sector,
        articles,
    )

    article_batches = build_token_limited_batches(
        articles,
        context_limit=context_limit,
        prompt_overhead_tokens=prompt_overhead_tokens,
    )

    cleaned_impacts = _collect_cleaned_impacts(
        article_batches,
        sector=sector,
        client=client,
        model=model,
        valid_article_ids=valid_article_ids,
        valid_sector_id=valid_sector_id,
        valid_sector_name=valid_sector_name,
        system_prompt_override=system_prompt_override,
        task_override=task_override,
    )

    return {
        "sector": sector,
        "impacts": cleaned_impacts,
    }


def classify_sector_articles_to_companies(
    sector_identifier: str,
    **kwargs: Any,
) -> dict[str, Any]:
    return classify_sector_articles(sector_identifier, **kwargs)


if __name__ == "__main__":
    print(json.dumps(classify_sector_articles("technology"), ensure_ascii=True, indent=2))
