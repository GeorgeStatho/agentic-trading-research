from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

AGENT_CALLERS_DIR = Path(__file__).resolve().parent
if str(AGENT_CALLERS_DIR) not in sys.path:
    sys.path.append(str(AGENT_CALLERS_DIR))

from _shared import Client, ask_ollama_model, build_token_limited_batches, extract_json_object, get_ollama_client, parse_published_at
from db_helpers import DB_PATH, get_all_sectors, get_connection
from db_helpers import (
    add_us_news_sector_impact,
    add_world_news_sector_impact,
    initialize_news_database,
    mark_us_news_article_processed,
    mark_world_news_article_processed,
)


OLLAMA_HOST = os.getenv("MACRO_NEWS_OLLAMA_HOST", os.getenv("WORLD_NEWS_OLLAMA_HOST", "http://127.0.0.1:11434"))
DEFAULT_MODEL = os.getenv("MACRO_NEWS_MODEL", os.getenv("WORLD_NEWS_MODEL", "world-news-sectors"))

DEFAULT_MAX_ARTICLE_AGE_DAYS = 3
DEFAULT_CONTEXT_LIMIT = 4096
DEFAULT_PROMPT_OVERHEAD_TOKENS = 1200
macro_news_classifier = get_ollama_client(OLLAMA_HOST)

ImpactSaver = Callable[..., int]
ProcessedMarker = Callable[..., int]

SCOPE_CONFIG: dict[str, dict[str, Any]] = {
    "world": {
        "label": "world",
        "membership_table": "world_news_articles",
        "processing_table": "world_news_article_processing",
        "impact_saver": add_world_news_sector_impact,
        "processed_marker": mark_world_news_article_processed,
    },
    "us": {
        "label": "U.S.",
        "membership_table": "us_news_articles",
        "processing_table": "us_news_article_processing",
        "impact_saver": add_us_news_sector_impact,
        "processed_marker": mark_us_news_article_processed,
    },
}


def _get_scope_config(news_scope: str) -> dict[str, Any]:
    normalized_scope = str(news_scope or "").strip().lower()
    config = SCOPE_CONFIG.get(normalized_scope)
    if config is None:
        raise ValueError(f"Unsupported news scope: {news_scope!r}")
    return config


def ask_model(client: Client, model: str, system_prompt: str, user_prompt: str) -> str:
    return ask_ollama_model(
        client,
        model,
        system_prompt,
        user_prompt,
        temperature=0,
        host_label=OLLAMA_HOST,
    )


def get_recent_macro_news_articles(
    news_scope: str,
    *,
    max_age_days: int = DEFAULT_MAX_ARTICLE_AGE_DAYS,
) -> list[dict[str, Any]]:
    config = _get_scope_config(news_scope)
    cutoff = datetime.now(timezone.utc)
    articles: list[dict[str, Any]] = []

    membership_table = config["membership_table"]
    processing_table = config["processing_table"]

    with get_connection(DB_PATH) as conn:
        rows = conn.execute(
            f"""
            SELECT
                na.id AS article_id,
                na.title,
                na.body,
                na.summary,
                na.source,
                na.source_url,
                na.published_at
            FROM {membership_table} AS mna
            JOIN news_articles AS na ON na.id = mna.article_id
            LEFT JOIN {processing_table} AS pna ON pna.article_id = na.id
            WHERE pna.article_id IS NULL
            ORDER BY na.published_at DESC, na.id DESC
            """
        ).fetchall()

    for row in rows:
        published_at = parse_published_at(row["published_at"])
        if published_at is None:
            continue

        age_days = (cutoff - published_at).total_seconds() / 86400
        if age_days > max_age_days:
            continue

        articles.append(
            {
                "article_id": row["article_id"],
                "title": row["title"] or "",
                "body": row["body"] or "",
                "summary": row["summary"] or "",
                "source": row["source"] or "",
                "source_url": row["source_url"] or "",
                "published_at": row["published_at"] or "",
            }
        )

    return articles


def get_sector_reference() -> list[dict[str, Any]]:
    sectors = get_all_sectors()
    return [
        {
            "sector_id": sector["id"],
            "sector_key": sector["sector_key"],
            "sector_name": sector["name"],
        }
        for sector in sectors
    ]


def build_macro_news_to_sectors_prompt(
    articles: list[dict[str, Any]],
    sectors: list[dict[str, Any]],
    *,
    news_scope: str,
) -> tuple[str, str]:
    config = _get_scope_config(news_scope)
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


def _save_batch_results(
    article_batch: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    *,
    model: str,
    raw_response: str,
    news_scope: str,
) -> None:
    config = _get_scope_config(news_scope)
    impact_saver: ImpactSaver = config["impact_saver"]
    processed_marker: ProcessedMarker = config["processed_marker"]

    batch_pairs_by_article: dict[int, list[dict[str, Any]]] = {}
    for pair in pairs:
        article_id = int(pair["article_id"])
        batch_pairs_by_article.setdefault(article_id, []).append(pair)

    with get_connection(DB_PATH) as conn:
        for pair in pairs:
            impact_saver(
                article_id=int(pair["article_id"]),
                sector_id=int(pair["sector_id"]),
                confidence=str(pair["confidence"]),
                reason=str(pair["reason"]),
                raw_json=pair,
                conn=conn,
            )

        for article in article_batch:
            article_id = int(article["article_id"])
            processed_marker(
                article_id=article_id,
                model=model,
                raw_json={
                    "news_scope": news_scope,
                    "article_id": article_id,
                    "pairs": batch_pairs_by_article.get(article_id, []),
                    "raw_response": raw_response,
                },
                conn=conn,
            )


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
    if not articles:
        return []

    sectors = get_sector_reference()

    valid_sector_ids = {sector["sector_id"] for sector in sectors}
    valid_sector_keys = {sector["sector_key"] for sector in sectors}
    valid_article_ids = {article["article_id"] for article in articles}

    cleaned_pairs: list[dict[str, Any]] = []
    seen_pairs: set[tuple[int, int]] = set()

    article_batches = build_token_limited_batches(
        articles,
        context_limit=context_limit,
        prompt_overhead_tokens=prompt_overhead_tokens,
    )

    for article_batch in article_batches:
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
        if not isinstance(pairs, list):
            continue

        batch_cleaned_pairs: list[dict[str, Any]] = []

        for pair in pairs:
            if not isinstance(pair, dict):
                continue

            try:
                article_id = int(pair.get("article_id"))
                sector_id = int(pair.get("sector_id"))
            except (TypeError, ValueError):
                continue

            sector_key = str(pair.get("sector_key") or "").strip()
            confidence = str(pair.get("confidence") or "").strip().lower()
            reason = str(pair.get("reason") or "").strip()

            if article_id not in valid_article_ids:
                continue
            if sector_id not in valid_sector_ids:
                continue
            if sector_key not in valid_sector_keys:
                continue
            if confidence not in {"high", "medium", "low"}:
                continue
            if (article_id, sector_id) in seen_pairs:
                continue

            cleaned_pair = {
                "article_id": article_id,
                "sector_id": sector_id,
                "sector_key": sector_key,
                "confidence": confidence,
                "reason": reason,
            }
            cleaned_pairs.append(cleaned_pair)
            batch_cleaned_pairs.append(cleaned_pair)
            seen_pairs.add((article_id, sector_id))

        _save_batch_results(
            article_batch,
            batch_cleaned_pairs,
            model=model,
            raw_response=raw_response,
            news_scope=news_scope,
        )

    return cleaned_pairs


def classify_world_news_to_sectors(**kwargs: Any) -> list[dict[str, Any]]:
    return classify_macro_news_to_sectors(news_scope="world", **kwargs)


def classify_us_news_to_sectors(**kwargs: Any) -> list[dict[str, Any]]:
    return classify_macro_news_to_sectors(news_scope="us", **kwargs)


if __name__ == "__main__":
    pairs = classify_macro_news_to_sectors(news_scope="world")
    print(json.dumps(pairs, indent=2))
