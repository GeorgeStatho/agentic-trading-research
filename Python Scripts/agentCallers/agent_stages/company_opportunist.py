from __future__ import annotations

from datetime import datetime
import json
import logging
from typing import Any

from services.core.settings import get_settings
from services.core.settings import StageModel
from agent_helpers.company_opportunist import (
    build_company_opportunist_articles,
    build_company_valid_reference_sets,
    build_empty_company_result,
    extract_company_impacts,
    get_company_reference,
    normalize_company_impact,
    save_company_opportunist_batch_results,
)
from agent_helpers.opportunist_support import build_shared_opportunist_impacts_schema, classifier_batch_token_limit
from agent_helpers.llm_client import Client, ModelAnswer, ask_classifier_model, get_model_client
from agent_helpers.text_utils import build_token_limited_batches, extract_json_value


def _stage_model() -> StageModel:
    return get_settings().models.company_opportunist


DEFAULT_PROMPT_OVERHEAD_TOKENS = 1200

_company_opportunist_client: Client | None = None
LOGGER = logging.getLogger(__name__)
_COMPANY_IMPACT_LIST: dict[str, Any] = build_shared_opportunist_impacts_schema(
    additional_properties={
        "relative_positioning": {
            "type": "string",
            "enum": [
                "better_than_peers",
                "worse_than_peers",
                "similar",
                "not_applicable",
            ],
        },
    },
    additional_required=["relative_positioning"],
)["properties"]["impacts"]
# One result per supplied article, so an article the model skipped can be told apart from one with no impact.
COMPANY_IMPACTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "articles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"article_id": {"type": "integer"}, "impacts": _COMPANY_IMPACT_LIST},
                "required": ["article_id", "impacts"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["articles"],
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
        _company_opportunist_client = get_model_client(_stage_model().ollama_host, _stage_model().provider, stage="company_opportunist")
    return _company_opportunist_client


def ask_model(client: Client, model: str, system_prompt: str, user_prompt: str) -> ModelAnswer:
    return ask_classifier_model(
        client,
        model,
        system_prompt,
        user_prompt,
        temperature=0,
        host_label=_stage_model().ollama_host,
        thinking_level=_stage_model().thinking_level,
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
        "top_companies": list(peer_groups.get("top_companies") or [])[:get_settings().opportunists.company_top_companies_limit],
        "top_growth_companies": list(peer_groups.get("top_growth_companies") or [])[:get_settings().opportunists.company_ranked_companies_limit],
        "top_performing_companies": list(peer_groups.get("top_performing_companies") or [])[:get_settings().opportunists.company_ranked_companies_limit],
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
    # Build the company opportunist prompt with the target company, article set, and schema instructions.
    default_system_prompt = (
        "You are a market analyst that maps company-specific news to likely stock impact for one company. "
        "Use the supplied peer group context to judge whether this company is helped or hurt relative to comparable companies. "
        "Return only valid JSON: an object with a top-level key named 'articles' holding one entry for every supplied article, "
        "each with that article's article_id and an impacts array. "
        "Do not include markdown fences, notes, or extra keys. "
        "Each impact item must contain: confidence, impact_direction, impact_magnitude, materiality, "
        "time_horizon, effect_type, relative_positioning, and reason. "
        "Do not include company_id. Do not include symbol. "
        "Do not include article_id inside an impact item. "
        "confidence must be one of: high, medium, low. "
        "impact_direction must be one of: positive, negative, neutral, mixed. "
        "impact_magnitude must be one of: major, moderate, modest, minimal. "
        "materiality must be one of: high, medium, low. "
        "impact_magnitude = size of the likely effect if the thesis is real. "
        "materiality = how important or relevant the article is for this company. "
        "time_horizon must be one of: immediate, short_term, medium_term, unclear. "
        "effect_type must be one of: direct, indirect. "
        "relative_positioning must be one of: better_than_peers, worse_than_peers, similar, not_applicable. "
        "Return an empty impacts array if the article is not materially relevant. "
        "Use neutral or mixed when the impact is unclear or two-sided. "
        "Reason must explain the causal chain. "
        "Do not classify sentiment alone; focus on likely business or investor impact. "
        "Prefer omission over weak guesses. "
        "Only include the supplied company. Do not invent other companies."
    )
    system_prompt = str(system_prompt_override or default_system_prompt)

    payload = {
        "task": str(
            task_override
            or "Map each company-linked article to the likely impact on the supplied company and compare that impact against the supplied peer group."
        ),
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
                    limit=get_settings().opportunists.company_article_body_char_limit,
                ),
                "published_at": article["published_at"],
                "source": article["source"],
                "source_url": article["source_url"],
                "source_page_url": article.get("source_page_url", ""),
            }
            for article in articles
        ],
        "required_output": {
            "articles": [
                {
                    "article_id": "integer, copied from the supplied article",
                    "impacts": [
                        {
                            "confidence": "high|medium|low",
                            "impact_direction": "positive|negative|neutral|mixed",
                            "impact_magnitude": "major|moderate|modest|minimal",
                            "materiality": "high|medium|low",
                            "time_horizon": "immediate|short_term|medium_term|unclear",
                            "effect_type": "direct|indirect",
                            "relative_positioning": "better_than_peers|worse_than_peers|similar|not_applicable",
                            "reason": "short causal explanation",
                        }
                    ],
                }
            ]
        },
    }
    return system_prompt, json.dumps(payload, ensure_ascii=True)


def _results_by_article(parsed: Any, article_batch: list[dict[str, Any]]) -> dict[int, list[Any]]:
    """Impacts per supplied article. An article the answer does not name is absent, not empty."""
    batch_ids = {int(article["article_id"]) for article in article_batch}
    if isinstance(parsed, dict) and isinstance(parsed.get("articles"), list):
        found: dict[int, list[Any]] = {}
        for entry in parsed["articles"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("impacts"), list):
                continue
            try:
                article_id = int(entry.get("article_id"))
            except (TypeError, ValueError):
                continue
            if article_id in batch_ids:
                found.setdefault(article_id, []).extend(entry["impacts"])
        return found
    # A lone article may still come back in the older flat impacts shape.
    impacts = extract_company_impacts(parsed)
    if len(article_batch) == 1 and isinstance(impacts, list):
        return {next(iter(batch_ids)): impacts}
    return {}


def _classify_article_batch(
    article_batch: list[dict[str, Any]],
    *,
    company: dict[str, Any],
    peer_groups: dict[str, Any],
    client: Client,
    model: str,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
) -> tuple[ModelAnswer, dict[int, list[Any]]]:
    # Classify the current batch and return each named article's raw impacts.
    system_prompt, user_prompt = build_company_opportunist_prompt(
        company,
        peer_groups,
        article_batch,
        system_prompt_override=system_prompt_override,
        task_override=task_override,
    )
    answer = ask_model(
        client=client,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    return answer, _results_by_article(extract_json_value(answer.text), article_batch)


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
    # Collect the cleaned impacts into one cleaned result for the next stage.
    cleaned_impacts: list[dict[str, Any]] = []
    seen_impacts: set[tuple[int, int]] = set()

    def classify_and_save(article_batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Save every article the answer covers; return the ones it left out."""
        try:
            answer, results = _classify_article_batch(
                article_batch,
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
            if len(article_batch) > 1:
                LOGGER.warning("Company batch of %s articles for %s hit MAX_TOKENS; retrying them one by one.",
                               len(article_batch), valid_symbol)
                return article_batch
            answer = ModelAnswer(text=str(exc), provider=client.provider, model=model)
            results = {}
            LOGGER.warning(
                "Skipping company article %s for %s because the model hit MAX_TOKENS and returned no JSON content.",
                int(article_batch[0]["article_id"]),
                valid_symbol,
            )
        if len(article_batch) == 1:
            # Alone, an answer that names nothing means no impact, as it did before batching.
            results.setdefault(int(article_batch[0]["article_id"]), [])

        answered = [article for article in article_batch if int(article["article_id"]) in results]
        missing = [article for article in article_batch if int(article["article_id"]) not in results]
        batch_cleaned_impacts: list[dict[str, Any]] = []
        for article in answered:
            for impact in results[int(article["article_id"])]:
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
                )
                if dedupe_key in seen_impacts:
                    continue

                cleaned_impacts.append(normalized_impact)
                batch_cleaned_impacts.append(normalized_impact)
                seen_impacts.add(dedupe_key)

        if answered and not batch_cleaned_impacts:
            LOGGER.warning(
                "No valid company impacts were extracted for company %s from articles %s. Raw model response: %s",
                valid_symbol,
                [int(article["article_id"]) for article in answered],
                answer.text,
            )
        if answered:
            save_company_opportunist_batch_results(
                answered,
                batch_cleaned_impacts,
                company_id=valid_company_id,
                answer=answer,
            )
        if missing:
            LOGGER.warning("The model left out %s of %s company articles for %s; retrying them one by one.",
                           len(missing), len(article_batch), valid_symbol)
        return missing

    for article_batch in article_batches:
        for article in classify_and_save(article_batch):
            classify_and_save([article])

    return cleaned_impacts


def classify_company_articles(
    company_identifier: str,
    *,
    client: Client | None = None,
    model: str | None = None,
    system_prompt_override: str | None = None,
    task_override: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    max_age_days: int | None = None,
    context_limit: int | None = None,
    prompt_overhead_tokens: int = DEFAULT_PROMPT_OVERHEAD_TOKENS,
) -> dict[str, Any]:
    # Batch company articles through the LLM, clean the responses, and persist the resulting impacts.
    model = model or _stage_model().model
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
        context_limit=context_limit or classifier_batch_token_limit(client.provider),
        prompt_overhead_tokens=prompt_overhead_tokens,
        body_char_limit=get_settings().opportunists.company_article_body_char_limit,
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
