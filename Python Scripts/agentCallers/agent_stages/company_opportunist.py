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

from _company_opportunist_helpers import (
    build_company_opportunist_articles,
    build_company_valid_reference_sets,
    build_empty_company_result,
    extract_company_impacts,
    get_company_reference,
    normalize_company_impact,
    save_company_opportunist_batch_results,
)
from _shared import (
    Client,
    ask_llm_model,
    build_token_limited_batches,
    extract_json_value,
    get_model_client,
)


OLLAMA_HOST = os.getenv(
    "COMPANY_OPPURUNTIST_OLLAMA_HOST",
    os.getenv("MACRO_NEWS_OLLAMA_HOST", os.getenv("WORLD_NEWS_OLLAMA_HOST", "http://127.0.0.1:11434")),
)
DEFAULT_MODEL = os.getenv(
    "COMPANY_OPPURUNTIST_MODEL",
    os.getenv("MACRO_NEWS_MODEL", os.getenv("WORLD_NEWS_MODEL", "world-news-sectors")),
)

DEFAULT_CONTEXT_LIMIT = 4096
DEFAULT_PROMPT_OVERHEAD_TOKENS = 1200

_company_opportunist_client: Client | None = None
LOGGER = logging.getLogger(__name__)
DEFAULT_TOP_COMPANIES_LIMIT = max(1, int(os.getenv("COMPANY_OPPORTUNIST_TOP_COMPANIES_LIMIT", "12")))
DEFAULT_RANKED_COMPANIES_LIMIT = max(1, int(os.getenv("COMPANY_OPPORTUNIST_RANKED_COMPANIES_LIMIT", "6")))
DEFAULT_ARTICLE_BODY_CHAR_LIMIT = max(200, int(os.getenv("COMPANY_OPPORTUNIST_ARTICLE_BODY_CHAR_LIMIT", "1200")))
COMPANY_IMPACTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "impacts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "impact_direction": {"type": "string", "enum": ["positive", "negative"]},
                    "impact_magnitude": {"type": "string", "enum": ["major", "moderate", "modest"]},
                    "reason": {"type": "string"},
                },
                "required": [
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
    "build_company_opportunist_articles",
    "build_company_opportunist_prompt",
    "classify_company_articles",
    "get_company_reference",
]


def _get_default_client() -> Client:
    global _company_opportunist_client
    if _company_opportunist_client is None:
        _company_opportunist_client = get_model_client(OLLAMA_HOST)
    return _company_opportunist_client


def ask_model(client: Client, model: str, system_prompt: str, user_prompt: str) -> str:
    return ask_llm_model(
        client,
        model,
        system_prompt,
        user_prompt,
        temperature=0,
        host_label=OLLAMA_HOST,
        response_schema=COMPANY_IMPACTS_SCHEMA,
    )


def _truncate_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _build_trimmed_peer_groups(peer_groups: dict[str, Any]) -> dict[str, Any]:
    return {
        "industry": dict(peer_groups.get("industry") or {}),
        "top_companies": list(peer_groups.get("top_companies") or [])[:DEFAULT_TOP_COMPANIES_LIMIT],
        "top_growth_companies": list(peer_groups.get("top_growth_companies") or [])[:DEFAULT_RANKED_COMPANIES_LIMIT],
        "top_performing_companies": list(peer_groups.get("top_performing_companies") or [])[:DEFAULT_RANKED_COMPANIES_LIMIT],
    }


def _is_empty_vertex_max_tokens_error(exc: Exception) -> bool:
    message = str(exc or "")
    normalized = message.upper()
    return "MODEL RETURNED EMPTY CONTENT" in normalized and "MAX_TOKENS" in normalized


def build_company_opportunist_prompt(
    company: dict[str, Any],
    peer_groups: dict[str, Any],
    articles: list[dict[str, Any]],
    *,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> tuple[str, str]:
    default_system_prompt = (
        "You are a market analyst that maps company-specific news to likely stock impact for one company. "
        "Return only valid JSON. "
        "You may return either a top-level array or an object with a top-level key named 'impacts'. "
        "Do not include markdown fences, notes, or extra keys. "
        "Each impact item must contain: confidence, impact_direction, impact_magnitude, and reason. "
        "Do not include company_id. Do not include symbol. "
        "Do not include article_id. "
        "confidence must be one of: high, medium, low. "
        "impact_direction must be one of: positive, negative. "
        "impact_magnitude must be one of: major, moderate, modest. "
        "Only include the supplied company. Do not invent other companies."
    )
    system_prompt = str(system_prompt_override or default_system_prompt)

    payload = {
        "task": str(task_override or "Map each company-linked article to the likely impact on the supplied company."),
        "company": company,
        "peer_groups": _build_trimmed_peer_groups(peer_groups),
        "articles": [
            {
                "article_id": article["article_id"],
                "article_scope": article["article_scope"],
                "title": article["title"],
                "summary": article["summary"],
                "body": _truncate_text(
                    article["body"] or article["summary"],
                    limit=DEFAULT_ARTICLE_BODY_CHAR_LIMIT,
                ),
                "published_at": article["published_at"],
                "source": article["source"],
                "source_url": article["source_url"],
                "source_page_url": article.get("source_page_url", ""),
            }
            for article in articles
        ],
        "required_output": {
            "impacts": [
                {
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
    company: dict[str, Any],
    peer_groups: dict[str, Any],
    client: Client,
    model: str,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    system_prompt, user_prompt = build_company_opportunist_prompt(
        company,
        peer_groups,
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
    impacts = extract_company_impacts(parsed)
    return raw_response, impacts if isinstance(impacts, list) else []


def _collect_cleaned_impacts(
    article_batches: list[list[dict[str, Any]]],
    *,
    company: dict[str, Any],
    peer_groups: dict[str, Any],
    client: Client,
    model: str,
    valid_article_ids: set[int],
    valid_company_id: int,
    valid_symbol: str,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> list[dict[str, Any]]:
    cleaned_impacts: list[dict[str, Any]] = []
    seen_impacts: set[tuple[int, int, str, str]] = set()

    for article_batch in article_batches:
        for article in article_batch:
            single_article_batch = [article]
            try:
                raw_response, batch_impacts = _classify_article_batch(
                    single_article_batch,
                    company=company,
                    peer_groups=peer_groups,
                    client=client,
                    model=model,
                    system_prompt_override=system_prompt_override,
                    task_override=task_override,
                )
            except RuntimeError as exc:
                if not _is_empty_vertex_max_tokens_error(exc):
                    raise

                raw_response = str(exc)
                batch_impacts = []
                LOGGER.warning(
                    "Skipping company article %s for %s because the model hit MAX_TOKENS and returned no JSON content.",
                    int(article["article_id"]),
                    valid_symbol,
                )
            batch_cleaned_impacts: list[dict[str, Any]] = []

            for impact in batch_impacts:
                if not isinstance(impact, dict):
                    continue

                normalized_impact = normalize_company_impact(
                    impact,
                    source_article_id=int(article["article_id"]),
                    valid_company_id=valid_company_id,
                    valid_symbol=valid_symbol,
                )
                if normalized_impact is None:
                    continue

                dedupe_key = (
                    normalized_impact["article_id"],
                    normalized_impact["company_id"],
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
                    "No valid company impacts were extracted for company %s from article %s. Raw model response: %s",
                    valid_symbol,
                    int(article["article_id"]),
                    raw_response,
                )

            save_company_opportunist_batch_results(
                single_article_batch,
                batch_cleaned_impacts,
                company_id=valid_company_id,
                model=model,
                raw_response=raw_response,
            )

    return cleaned_impacts


def classify_company_articles(
    company_identifier: str,
    *,
    client: Client | None = None,
    model: str = DEFAULT_MODEL,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = None,
    context_limit: int = DEFAULT_CONTEXT_LIMIT,
    prompt_overhead_tokens: int = DEFAULT_PROMPT_OVERHEAD_TOKENS,
) -> dict[str, Any]:
    client = client or _get_default_client()
    company, peer_groups, articles = build_company_opportunist_articles(
        company_identifier,
        start_time=start_time,
        end_time=end_time,
        max_age_days=max_age_days,
    )

    if not articles:
        return build_empty_company_result(company, peer_groups)

    valid_company_id, valid_symbol, valid_article_ids = build_company_valid_reference_sets(
        company,
        articles,
    )

    article_batches = build_token_limited_batches(
        articles,
        context_limit=context_limit,
        prompt_overhead_tokens=prompt_overhead_tokens,
    )

    cleaned_impacts = _collect_cleaned_impacts(
        article_batches,
        company=company,
        peer_groups=peer_groups,
        client=client,
        model=model,
        valid_article_ids=valid_article_ids,
        valid_company_id=valid_company_id,
        valid_symbol=valid_symbol,
        system_prompt_override=system_prompt_override,
        task_override=task_override,
    )

    return {
        "company": company,
        "peer_groups": peer_groups,
        "impacts": cleaned_impacts,
    }


if __name__ == "__main__":
    print(json.dumps(classify_company_articles("AAPL"), ensure_ascii=True, indent=2))
