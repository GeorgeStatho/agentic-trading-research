from __future__ import annotations

from datetime import datetime
import json
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

from _sector_opportunist_helpers import (
    build_empty_sector_result,
    build_sector_opportunist_articles,
    build_sector_valid_reference_sets,
    extract_sector_impacts,
    get_sector_reference,
    normalize_sector_impact,
    save_sector_opportunist_batch_results,
)
from _shared import (
    Client,
    ask_ollama_model,
    build_token_limited_batches,
    extract_json_object,
    get_ollama_client,
)


OLLAMA_HOST = os.getenv(
    "SECTOR_OPPURUNTIST_OLLAMA_HOST",
    os.getenv("MACRO_NEWS_OLLAMA_HOST", os.getenv("WORLD_NEWS_OLLAMA_HOST", "http://127.0.0.1:11434")),
)
DEFAULT_MODEL = os.getenv(
    "SECTOR_OPPURUNTIST_MODEL",
    os.getenv("MACRO_NEWS_MODEL", os.getenv("WORLD_NEWS_MODEL", "world-news-sectors")),
)

DEFAULT_MAX_ARTICLE_AGE_DAYS = 3
DEFAULT_CONTEXT_LIMIT = 4096
DEFAULT_PROMPT_OVERHEAD_TOKENS = 1200

sector_opportunist = get_ollama_client(OLLAMA_HOST)

__all__ = [
    "build_sector_opportunist_articles",
    "build_sector_opportunist_prompt",
    "classify_sector_articles",
    "get_sector_reference",
]


def ask_model(client: Client, model: str, system_prompt: str, user_prompt: str) -> str:
    return ask_ollama_model(
        client,
        model,
        system_prompt,
        user_prompt,
        temperature=0,
        host_label=OLLAMA_HOST,
    )


def build_sector_opportunist_prompt(
    sector: dict[str, Any],
    articles: list[dict[str, Any]],
) -> tuple[str, str]:
    system_prompt = (
        "You are a market analyst that maps sector-level news to likely impact for one sector. "
        "Return only valid JSON with a top-level key named 'impacts'. "
        "Do not include markdown fences, notes, or extra keys. "
        "Each item in 'impacts' must contain: article_id, sector_id, sector_name, confidence, "
        "impact_direction, impact_magnitude, and reason. "
        "confidence must be one of: high, medium, low. "
        "impact_direction must be one of: positive, negative. "
        "impact_magnitude must be one of: major, moderate, modest. "
        "Only include the supplied sector."
    )

    payload = {
        "task": "Map each sector-linked article to the likely impact on the supplied sector.",
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
                    "article_id": "integer",
                    "sector_id": "integer",
                    "sector_name": "string",
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
    client: Client,
    model: str,
) -> tuple[str, list[dict[str, Any]]]:
    system_prompt, user_prompt = build_sector_opportunist_prompt(
        sector,
        article_batch,
    )
    raw_response = ask_model(
        client=client,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    parsed = extract_json_object(raw_response)
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
) -> list[dict[str, Any]]:
    cleaned_impacts: list[dict[str, Any]] = []
    seen_impacts: set[tuple[int, int, str, str]] = set()

    for article_batch in article_batches:
        raw_response, batch_impacts = _classify_article_batch(
            article_batch,
            sector=sector,
            client=client,
            model=model,
        )
        batch_cleaned_impacts: list[dict[str, Any]] = []

        for impact in batch_impacts:
            if not isinstance(impact, dict):
                continue

            normalized_impact = normalize_sector_impact(
                impact,
                valid_article_ids=valid_article_ids,
                valid_sector_id=valid_sector_id,
                valid_sector_name=valid_sector_name,
            )
            if normalized_impact is None:
                continue

            dedupe_key = (
                normalized_impact["article_id"],
                normalized_impact["sector_id"],
                normalized_impact["impact_direction"],
                normalized_impact["impact_magnitude"],
            )
            if dedupe_key in seen_impacts:
                continue

            cleaned_impacts.append(normalized_impact)
            batch_cleaned_impacts.append(normalized_impact)
            seen_impacts.add(dedupe_key)

        save_sector_opportunist_batch_results(
            article_batch,
            batch_cleaned_impacts,
            model=model,
            raw_response=raw_response,
        )

    return cleaned_impacts


def classify_sector_articles(
    sector_identifier: str,
    *,
    client: Client = sector_opportunist,
    model: str = DEFAULT_MODEL,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
    context_limit: int = DEFAULT_CONTEXT_LIMIT,
    prompt_overhead_tokens: int = DEFAULT_PROMPT_OVERHEAD_TOKENS,
) -> dict[str, Any]:
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
