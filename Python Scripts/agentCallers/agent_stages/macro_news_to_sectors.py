from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sys
import time
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

from _macro_news_helpers import (
    get_recent_macro_news_articles,
    get_scope_config,
    get_sector_reference,
    save_batch_results,
)
from _shared import Client, ask_ollama_model, build_token_limited_batches, extract_json_object, get_ollama_client
from db_helpers import initialize_news_database


OLLAMA_HOST = os.getenv("MACRO_NEWS_OLLAMA_HOST", os.getenv("WORLD_NEWS_OLLAMA_HOST", "http://127.0.0.1:11434"))
DEFAULT_MODEL = os.getenv("MACRO_NEWS_MODEL", os.getenv("WORLD_NEWS_MODEL", "world-news-sectors"))

DEFAULT_MAX_ARTICLE_AGE_DAYS = 3
DEFAULT_CONTEXT_LIMIT = 4096
DEFAULT_PROMPT_OVERHEAD_TOKENS = 1200
DEFAULT_BATCH_PAUSE_SECONDS = max(
    0.0,
    float(os.getenv("MACRO_NEWS_BATCH_PAUSE_SECONDS", "0.75")),
)
macro_news_classifier = get_ollama_client(OLLAMA_HOST)
LOGGER = logging.getLogger(__name__)


def _configure_console_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
        force=True,
    )


def ask_model(client: Client, model: str, system_prompt: str, user_prompt: str) -> str:
    return ask_ollama_model(
        client,
        model,
        system_prompt,
        user_prompt,
        temperature=0,
        host_label=OLLAMA_HOST,
    )


def build_macro_news_to_sectors_prompt(
    articles: list[dict[str, Any]],
    sectors: list[dict[str, Any]],
    *,
    news_scope: str,
) -> tuple[str, str]:
    config = get_scope_config(news_scope)
    scope_label = config["label"]
    system_prompt = (
        f"You classify {scope_label} macro and business news into affected stock-market sectors. "
        "Use only the sectors provided by the user. "
        "Return only valid JSON with a top-level key named 'pairs'. "
        "Do not include markdown fences, notes, or extra keys. "
        "Each item in 'pairs' must contain: article_id, sector_id, sector_key, confidence, and reason. "
        "Confidence must be one of: high, medium, low. "
        "Do not invent sectors. "
        "If an article does not clearly affect a sector, omit it."
    )

    payload = {
        "task": f"Map each {scope_label.lower()}-news article to the sectors materially affected by the news.",
        "sectors": sectors,
        "articles": [
            {
                "article_id": article["article_id"],
                "title": article["title"],
                "body": article["body"] or article["summary"],
                "published_at": article["published_at"],
                "source": article["source"],
                "source_url": article["source_url"],
            }
            for article in articles
        ],
        "required_output": {
            "pairs": [
                {
                    "article_id": "integer",
                    "sector_id": "integer",
                    "sector_key": "string",
                    "confidence": "high|medium|low",
                    "reason": "short explanation",
                }
            ]
        },
    }
    user_prompt = json.dumps(payload, ensure_ascii=True)
    return system_prompt, user_prompt


def _extract_pairs(payload: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    if not isinstance(payload, dict):
        return None
    pairs = payload.get("pairs")
    if isinstance(pairs, list):
        return pairs
    output_schema = payload.get("output_schema")
    if isinstance(output_schema, dict):
        nested_pairs = output_schema.get("pairs")
        if isinstance(nested_pairs, list):
            return nested_pairs
    required_output = payload.get("required_output")
    if isinstance(required_output, dict):
        nested_pairs = required_output.get("pairs")
        if isinstance(nested_pairs, list):
            return nested_pairs
    return None


def _build_valid_reference_sets(
    sectors: list[dict[str, Any]],
    articles: list[dict[str, Any]],
) -> tuple[set[int], set[str], set[int]]:
    valid_sector_ids = {sector["sector_id"] for sector in sectors}
    valid_sector_keys = {sector["sector_key"] for sector in sectors}
    valid_article_ids = {article["article_id"] for article in articles}
    return valid_sector_ids, valid_sector_keys, valid_article_ids


def _classify_article_batch(
    article_batch: list[dict[str, Any]],
    *,
    sectors: list[dict[str, Any]],
    news_scope: str,
    client: Client,
    model: str,
) -> tuple[str, list[dict[str, Any]]]:
    system_prompt, user_prompt = build_macro_news_to_sectors_prompt(
        article_batch,
        sectors,
        news_scope=news_scope,
    )
    raw_response = ask_model(
        client=client,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    parsed = extract_json_object(raw_response)
    pairs = _extract_pairs(parsed)
    return raw_response, pairs if isinstance(pairs, list) else []


def _normalize_pair(
    pair: dict[str, Any],
    *,
    valid_article_ids: set[int],
    valid_sector_ids: set[int],
    valid_sector_keys: set[str],
) -> dict[str, Any] | None:
    try:
        article_id = int(pair.get("article_id"))
        sector_id = int(pair.get("sector_id"))
    except (TypeError, ValueError):
        return None

    sector_key = str(pair.get("sector_key") or "").strip()
    confidence = str(pair.get("confidence") or "").strip().lower()
    reason = str(pair.get("reason") or "").strip()

    if article_id not in valid_article_ids:
        return None
    if sector_id not in valid_sector_ids:
        return None
    if sector_key not in valid_sector_keys:
        return None
    if confidence not in {"high", "medium", "low"}:
        return None

    return {
        "article_id": article_id,
        "sector_id": sector_id,
        "sector_key": sector_key,
        "confidence": confidence,
        "reason": reason,
    }


def _collect_cleaned_pairs(
    article_batches: list[list[dict[str, Any]]],
    *,
    sectors: list[dict[str, Any]],
    news_scope: str,
    client: Client,
    model: str,
    valid_article_ids: set[int],
    valid_sector_ids: set[int],
    valid_sector_keys: set[str],
) -> list[dict[str, Any]]:
    cleaned_pairs: list[dict[str, Any]] = []
    seen_pairs: set[tuple[int, int]] = set()

    for batch_index, article_batch in enumerate(article_batches):
        if (
            batch_index > 0
            and client.provider == "vertex"
            and DEFAULT_BATCH_PAUSE_SECONDS > 0
        ):
            time.sleep(DEFAULT_BATCH_PAUSE_SECONDS)

        raw_response, batch_pairs = _classify_article_batch(
            article_batch,
            sectors=sectors,
            news_scope=news_scope,
            client=client,
            model=model,
        )

        batch_cleaned_pairs: list[dict[str, Any]] = []
        for pair in batch_pairs:
            if not isinstance(pair, dict):
                continue

            cleaned_pair = _normalize_pair(
                pair,
                valid_article_ids=valid_article_ids,
                valid_sector_ids=valid_sector_ids,
                valid_sector_keys=valid_sector_keys,
            )
            if cleaned_pair is None:
                continue

            dedupe_key = (cleaned_pair["article_id"], cleaned_pair["sector_id"])
            if dedupe_key in seen_pairs:
                continue

            cleaned_pairs.append(cleaned_pair)
            batch_cleaned_pairs.append(cleaned_pair)
            seen_pairs.add(dedupe_key)

        if not batch_cleaned_pairs:
            article_ids = [int(article["article_id"]) for article in article_batch]
            LOGGER.warning(
                "No valid sector pairs were extracted for %s article batch %s. Raw model response: %s",
                news_scope,
                article_ids,
                raw_response,
            )

        save_batch_results(
            article_batch,
            batch_cleaned_pairs,
            model=model,
            raw_response=raw_response,
            news_scope=news_scope,
        )

    return cleaned_pairs


def classify_macro_news_to_sectors(
    *,
    news_scope: str,
    client: Client = macro_news_classifier,
    model: str = DEFAULT_MODEL,
    max_age_days: int = DEFAULT_MAX_ARTICLE_AGE_DAYS,
    context_limit: int = DEFAULT_CONTEXT_LIMIT,
    prompt_overhead_tokens: int = DEFAULT_PROMPT_OVERHEAD_TOKENS,
) -> list[dict[str, Any]]:
    initialize_news_database()
    articles = get_recent_macro_news_articles(news_scope, max_age_days=max_age_days)
    LOGGER.info(
        "Found %s recent unprocessed %s articles for macro-to-sector classification",
        len(articles),
        news_scope,
    )
    if not articles:
        return []

    sectors = get_sector_reference()
    valid_sector_ids, valid_sector_keys, valid_article_ids = _build_valid_reference_sets(
        sectors,
        articles,
    )

    article_batches = build_token_limited_batches(
        articles,
        context_limit=context_limit,
        prompt_overhead_tokens=prompt_overhead_tokens,
    )
    LOGGER.info(
        "Built %s %s article batches for macro-to-sector classification",
        len(article_batches),
        news_scope,
    )
    cleaned_pairs = _collect_cleaned_pairs(
        article_batches,
        sectors=sectors,
        news_scope=news_scope,
        client=client,
        model=model,
        valid_article_ids=valid_article_ids,
        valid_sector_ids=valid_sector_ids,
        valid_sector_keys=valid_sector_keys,
    )
    LOGGER.info(
        "Extracted %s cleaned %s sector pairs",
        len(cleaned_pairs),
        news_scope,
    )
    return cleaned_pairs


def classify_world_news_to_sectors(**kwargs: Any) -> list[dict[str, Any]]:
    return classify_macro_news_to_sectors(news_scope="world", **kwargs)


def classify_us_news_to_sectors(**kwargs: Any) -> list[dict[str, Any]]:
    return classify_macro_news_to_sectors(news_scope="us", **kwargs)


if __name__ == "__main__":
    _configure_console_logging()
    pairs = classify_macro_news_to_sectors(news_scope="us")
    print(json.dumps(pairs, indent=2))
