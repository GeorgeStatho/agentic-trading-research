from __future__ import annotations

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

AGENT_CALLERS_DIR = Path(__file__).resolve().parent
if str(AGENT_CALLERS_DIR) not in sys.path:
    sys.path.append(str(AGENT_CALLERS_DIR))

from _industry_opportunist_helpers import (
    build_empty_result,
    build_industry_opportunist_articles,
    build_valid_reference_sets,
    extract_impacts,
    get_sector_industry_reference,
    normalize_impact,
    save_industry_opportunist_batch_results,
)
from _shared import (
    Client,
    ask_ollama_model,
    build_token_limited_batches,
    extract_json_value,
    get_ollama_client,
)


OLLAMA_HOST = os.getenv(
    "INDUSTRY_OPPURUNTINST_OLLAMA_HOST",
    os.getenv("MACRO_NEWS_OLLAMA_HOST", os.getenv("WORLD_NEWS_OLLAMA_HOST", "http://127.0.0.1:11434")),
)
DEFAULT_MODEL = os.getenv(
    "INDUSTRY_OPPURUNTINST_MODEL",
    os.getenv("MACRO_NEWS_MODEL", os.getenv("WORLD_NEWS_MODEL", "world-news-sectors")),
)

DEFAULT_MAX_ARTICLE_AGE_DAYS = 5
DEFAULT_CONTEXT_LIMIT = 4096
DEFAULT_PROMPT_OVERHEAD_TOKENS = 1200

industry_opportunist = get_ollama_client(OLLAMA_HOST)
LOGGER = logging.getLogger(__name__)
INDUSTRY_IMPACTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "impacts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "industry_id": {"type": "integer"},
                    "industry_key": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "impact_direction": {"type": "string", "enum": ["positive", "negative"]},
                    "impact_magnitude": {"type": "string", "enum": ["major", "moderate", "modest"]},
                    "reason": {"type": "string"},
                },
                "required": [
                    "industry_id",
                    "industry_key",
                    "confidence",
                    "impact_direction",
                    "impact_magnitude",
                    "reason",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["impacts"],
    "additionalProperties": False,
}

__all__ = [
    "build_industry_opportunist_articles",
    "build_industry_opportunist_prompt",
    "classify_sector_articles_to_industries",
    "get_sector_industry_reference",
]


def ask_model(client: Client, model: str, system_prompt: str, user_prompt: str) -> str:
    return ask_ollama_model(
        client,
        model,
        system_prompt,
        user_prompt,
        temperature=0,
        host_label=OLLAMA_HOST,
        response_schema=INDUSTRY_IMPACTS_SCHEMA,
    )


def build_industry_opportunist_prompt(
    sector: dict[str, Any],
    industries: list[dict[str, Any]],
    articles: list[dict[str, Any]],
    *,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> tuple[str, str]:
    default_system_prompt = (
        "You are a market analyst that maps news to stock-market industries inside one sector. "
        "Use only the industries provided by the user. "
        "Return only valid JSON with a top-level key named 'impacts'. "
        "Do not include markdown fences, notes, or extra keys. "
        "Each item in 'impacts' must contain: industry_id, industry_key, confidence, "
        "impact_direction, impact_magnitude, and reason. "
        "Do not include article_id. "
        "confidence must be one of: high, medium, low. "
        "impact_direction must be one of: positive, negative. "
        "impact_magnitude must be one of: major, moderate, modest. "
        "Only include industries that belong to the supplied sector and are materially affected by the article."
    )
    system_prompt = str(system_prompt_override or default_system_prompt)

    payload = {
        "task": str(task_override or "Map each article to the industries in the supplied sector that are materially affected."),
        "sector": sector,
        "industries": industries,
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
                    "industry_id": "integer",
                    "industry_key": "string",
                    "confidence": "high|medium|low",
                    "impact_direction": "positive|negative",
                    "impact_magnitude": "major|moderate|modest",
                    "reason": "short explanation",
                }
            ]
        },
    }
    return system_prompt, json.dumps(payload, ensure_ascii=True)


def _classify_article_batch(
    article_batch: list[dict[str, Any]],
    *,
    sector: dict[str, Any],
    industries: list[dict[str, Any]],
    client: Client,
    model: str,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    system_prompt, user_prompt = build_industry_opportunist_prompt(
        sector,
        industries,
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
    impacts = extract_impacts(parsed)
    return raw_response, impacts if isinstance(impacts, list) else []


def _collect_cleaned_impacts(
    article_batches: list[list[dict[str, Any]]],
    *,
    sector: dict[str, Any],
    industries: list[dict[str, Any]],
    client: Client,
    model: str,
    valid_article_ids: set[int],
    valid_industry_ids: set[int],
    valid_industry_keys: set[str],
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> list[dict[str, Any]]:
    cleaned_impacts: list[dict[str, Any]] = []
    seen_impacts: set[tuple[int, int, str, str]] = set()

    for article_batch in article_batches:
        for article in article_batch:
            single_article_batch = [article]
            raw_response, batch_impacts = _classify_article_batch(
                single_article_batch,
                sector=sector,
                industries=industries,
                client=client,
                model=model,
                system_prompt_override=system_prompt_override,
                task_override=task_override,
            )
            batch_cleaned_impacts: list[dict[str, Any]] = []

            for impact in batch_impacts:
                if not isinstance(impact, dict):
                    continue

                normalized_impact = normalize_impact(
                    impact,
                    source_article_id=int(article["article_id"]),
                    valid_industry_ids=valid_industry_ids,
                    valid_industry_keys=valid_industry_keys,
                )
                if normalized_impact is None:
                    continue

                dedupe_key = (
                    normalized_impact["article_id"],
                    normalized_impact["industry_id"],
                    normalized_impact["impact_direction"],
                    normalized_impact["impact_magnitude"],
                )
                if dedupe_key in seen_impacts:
                    continue

                cleaned_impacts.append(normalized_impact)
                batch_cleaned_impacts.append(normalized_impact)
                seen_impacts.add(dedupe_key)

            if not batch_cleaned_impacts:
                LOGGER.warning(
                    "No valid industry impacts were extracted for sector %s from article %s. Raw model response: %s",
                    sector["sector_key"],
                    int(article["article_id"]),
                    raw_response,
                )

            save_industry_opportunist_batch_results(
                single_article_batch,
                batch_cleaned_impacts,
                model=model,
                raw_response=raw_response,
            )

    return cleaned_impacts


def classify_sector_articles_to_industries(
    sector_identifier: str,
    *,
    client: Client = industry_opportunist,
    model: str = DEFAULT_MODEL,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
    context_limit: int = DEFAULT_CONTEXT_LIMIT,
    prompt_overhead_tokens: int = DEFAULT_PROMPT_OVERHEAD_TOKENS,
) -> dict[str, Any]:
    sector, industries, articles = build_industry_opportunist_articles(
        sector_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )

    if not articles:
        return build_empty_result(sector, industries)

    valid_industry_ids, valid_industry_keys, valid_article_ids = build_valid_reference_sets(
        industries,
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
        industries=industries,
        client=client,
        model=model,
        valid_article_ids=valid_article_ids,
        valid_industry_ids=valid_industry_ids,
        valid_industry_keys=valid_industry_keys,
        system_prompt_override=system_prompt_override,
        task_override=task_override,
    )

    return {
        "sector": sector,
        "industries": industries,
        "impacts": cleaned_impacts,
    }


if __name__ == "__main__":
    print(json.dumps(classify_sector_articles_to_industries("technology"), ensure_ascii=True, indent=2))
