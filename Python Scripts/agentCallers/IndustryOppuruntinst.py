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

from OppurtunistPayloadBuilder import build_opportunist_input
from _shared import (
    Client,
    ask_ollama_model,
    build_token_limited_batches,
    extract_json_object,
    get_ollama_client,
)
from db_helpers import get_all_industries


OLLAMA_HOST = os.getenv(
    "INDUSTRY_OPPURUNTINST_OLLAMA_HOST",
    os.getenv("MACRO_NEWS_OLLAMA_HOST", os.getenv("WORLD_NEWS_OLLAMA_HOST", "http://127.0.0.1:11434")),
)
DEFAULT_MODEL = os.getenv(
    "INDUSTRY_OPPURUNTINST_MODEL",
    os.getenv("MACRO_NEWS_MODEL", os.getenv("WORLD_NEWS_MODEL", "world-news-sectors")),
)

DEFAULT_MAX_ARTICLE_AGE_DAYS = 3
DEFAULT_CONTEXT_LIMIT = 4096
DEFAULT_PROMPT_OVERHEAD_TOKENS = 1200

VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}
VALID_IMPACT_DIRECTIONS = {"positive", "negative"}
VALID_IMPACT_MAGNITUDES = {"major", "moderate", "modest"}

industry_opportunist = get_ollama_client(OLLAMA_HOST)

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
    )


def get_sector_industry_reference(sector_identifier: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = build_opportunist_input(sector_identifier, max_age_days=None)
    sector = payload["sector"]

    industries = [
        {
            "industry_id": industry["id"],
            "industry_key": industry["industry_key"],
            "industry_name": industry["name"],
            "symbol": industry.get("symbol") or "",
        }
        for industry in get_all_industries()
        if str(industry.get("sector_key") or "").strip().lower()
        == str(sector.get("sector_key") or "").strip().lower()
    ]

    return sector, industries


def _load_opportunist_payload(
    sector_identifier: str,
    *,
    start_time: datetime | None,
    end_time: datetime | None,
    max_age_days: int | None,
) -> dict[str, Any]:
    return build_opportunist_input(
        sector_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )


def _make_base_article_record(article: dict[str, Any], *, article_scope: str, sector_source: str) -> dict[str, Any]:
    return {
        "article_id": int(article["article_id"]),
        "title": article.get("title") or "",
        "summary": article.get("summary") or "",
        "body": article.get("body") or "",
        "source": article.get("source") or "",
        "source_url": article.get("source_url") or "",
        "published_at": article.get("published_at") or "",
        "article_scope": article_scope,
        "sector_context": {
            "sector_source": sector_source,
        },
    }


def _seed_articles_from_sector_rss(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    deduped_articles: dict[int, dict[str, Any]] = {}

    for article in payload.get("sector_rss_news", []):
        article_record = _make_base_article_record(
            article,
            article_scope="sector_rss",
            sector_source="cnbc_rss",
        )
        deduped_articles[article_record["article_id"]] = article_record

    return deduped_articles


def _merge_macro_articles(
    deduped_articles: dict[int, dict[str, Any]],
    payload: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    for article in payload.get("related_macro_news", []):
        article_id = int(article["article_id"])
        entry = deduped_articles.setdefault(
            article_id,
            _make_base_article_record(
                article,
                article_scope="macro_news",
                sector_source="macro_news_high_confidence",
            ),
        )
        entry["macro_context"] = {
            "news_scope": article.get("news_scope") or "",
            "confidence": article.get("confidence") or "",
            "reason": article.get("reason") or "",
        }
        if entry.get("article_scope") == "sector_rss":
            entry["article_scope"] = "sector_rss_and_macro_news"

    return deduped_articles


def _sort_articles(deduped_articles: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        deduped_articles.values(),
        key=lambda article: (
            str(article.get("published_at") or ""),
            int(article.get("article_id") or 0),
        ),
        reverse=True,
    )


def build_industry_opportunist_articles(
    sector_identifier: str,
    *,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = DEFAULT_MAX_ARTICLE_AGE_DAYS,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    payload = _load_opportunist_payload(
        sector_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )
    sector = payload["sector"]

    _, industries = get_sector_industry_reference(sector_identifier)

    deduped_articles = _seed_articles_from_sector_rss(payload)
    deduped_articles = _merge_macro_articles(deduped_articles, payload)
    articles = _sort_articles(deduped_articles)

    return sector, industries, articles


def build_industry_opportunist_prompt(
    sector: dict[str, Any],
    industries: list[dict[str, Any]],
    articles: list[dict[str, Any]],
) -> tuple[str, str]:
    system_prompt = (
        "You are a market analyst that maps news to stock-market industries inside one sector. "
        "Use only the industries provided by the user. "
        "Return only valid JSON with a top-level key named 'impacts'. "
        "Do not include markdown fences, notes, or extra keys. "
        "Each item in 'impacts' must contain: article_id, industry_id, industry_key, confidence, "
        "impact_direction, impact_magnitude, and reason. "
        "confidence must be one of: high, medium, low. "
        "impact_direction must be one of: positive, negative. "
        "impact_magnitude must be one of: major, moderate, modest. "
        "Only include industries that belong to the supplied sector and are materially affected by the article."
    )

    payload = {
        "task": "Map each article to the industries in the supplied sector that are materially affected.",
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
                    "article_id": "integer",
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


def _extract_impacts(payload: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    if not isinstance(payload, dict):
        return None

    impacts = payload.get("impacts")
    if isinstance(impacts, list):
        return impacts

    for key in ("output_schema", "required_output"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            nested_impacts = nested.get("impacts")
            if isinstance(nested_impacts, list):
                return nested_impacts

    return None


def _build_empty_result(sector: dict[str, Any], industries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "sector": sector,
        "industries": industries,
        "impacts": [],
    }


def _build_valid_reference_sets(
    industries: list[dict[str, Any]],
    articles: list[dict[str, Any]],
) -> tuple[set[int], set[str], set[int]]:
    valid_industry_ids = {industry["industry_id"] for industry in industries}
    valid_industry_keys = {industry["industry_key"] for industry in industries}
    valid_article_ids = {article["article_id"] for article in articles}
    return valid_industry_ids, valid_industry_keys, valid_article_ids


def _normalize_impact(
    impact: dict[str, Any],
    *,
    valid_article_ids: set[int],
    valid_industry_ids: set[int],
    valid_industry_keys: set[str],
) -> dict[str, Any] | None:
    try:
        article_id = int(impact.get("article_id"))
        industry_id = int(impact.get("industry_id"))
    except (TypeError, ValueError):
        return None

    industry_key = str(impact.get("industry_key") or "").strip()
    confidence = str(impact.get("confidence") or "").strip().lower()
    impact_direction = str(impact.get("impact_direction") or "").strip().lower()
    impact_magnitude = str(impact.get("impact_magnitude") or "").strip().lower()
    reason = str(impact.get("reason") or "").strip()

    if article_id not in valid_article_ids:
        return None
    if industry_id not in valid_industry_ids:
        return None
    if industry_key not in valid_industry_keys:
        return None
    if confidence not in VALID_CONFIDENCE_LEVELS:
        return None
    if impact_direction not in VALID_IMPACT_DIRECTIONS:
        return None
    if impact_magnitude not in VALID_IMPACT_MAGNITUDES:
        return None
    if not reason:
        return None

    return {
        "article_id": article_id,
        "industry_id": industry_id,
        "industry_key": industry_key,
        "confidence": confidence,
        "impact_direction": impact_direction,
        "impact_magnitude": impact_magnitude,
        "reason": reason,
    }


def _classify_article_batch(
    article_batch: list[dict[str, Any]],
    *,
    sector: dict[str, Any],
    industries: list[dict[str, Any]],
    client: Client,
    model: str,
) -> list[dict[str, Any]]:
    system_prompt, user_prompt = build_industry_opportunist_prompt(
        sector,
        industries,
        article_batch,
    )
    raw_response = ask_model(
        client=client,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    parsed = extract_json_object(raw_response)
    impacts = _extract_impacts(parsed)
    return impacts if isinstance(impacts, list) else []


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
) -> list[dict[str, Any]]:
    cleaned_impacts: list[dict[str, Any]] = []
    seen_impacts: set[tuple[int, int, str, str]] = set()

    for article_batch in article_batches:
        batch_impacts = _classify_article_batch(
            article_batch,
            sector=sector,
            industries=industries,
            client=client,
            model=model,
        )

        for impact in batch_impacts:
            if not isinstance(impact, dict):
                continue

            normalized_impact = _normalize_impact(
                impact,
                valid_article_ids=valid_article_ids,
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
            seen_impacts.add(dedupe_key)

    return cleaned_impacts


def classify_sector_articles_to_industries(
    sector_identifier: str,
    *,
    client: Client = industry_opportunist,
    model: str = DEFAULT_MODEL,
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
        return _build_empty_result(sector, industries)

    valid_industry_ids, valid_industry_keys, valid_article_ids = _build_valid_reference_sets(
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
    )

    return {
        "sector": sector,
        "industries": industries,
        "impacts": cleaned_impacts,
    }


if __name__ == "__main__":
    print(json.dumps(classify_sector_articles_to_industries("technology"), ensure_ascii=True, indent=2))
