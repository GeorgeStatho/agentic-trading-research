from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent_contracts import (
    AgentSignalPayload,
    SupportingArticlesPayload,
    SupportingArticleSummaryPayload,
    SupportingFullArticlePayload,
)
from agent_helpers.shared import parse_published_at
from agent_helpers.strategist import HIGH_CONFIDENCE


def _serialize_signal(item: dict[str, Any], *, layer: str) -> AgentSignalPayload:
    signal: AgentSignalPayload = {
        "layer": layer,
        "article_id": item["article_id"],
        "confidence": item.get("confidence") or "",
        "reason": item.get("reason") or "",
        "published_at": item.get("published_at") or "",
        "source": item.get("source") or "",
        "source_url": item.get("source_url") or "",
        "title": item.get("title") or "",
        "summary": item.get("summary") or "",
    }
    if item.get("impact_direction"):
        signal["impact_direction"] = item["impact_direction"]
    if item.get("impact_magnitude"):
        signal["impact_magnitude"] = item["impact_magnitude"]
    if item.get("materiality"):
        signal["materiality"] = item["materiality"]
    if item.get("time_horizon"):
        signal["time_horizon"] = item["time_horizon"]
    if item.get("effect_type"):
        signal["effect_type"] = item["effect_type"]
    if item.get("relative_positioning"):
        signal["relative_positioning"] = item["relative_positioning"]
    if item.get("news_scope"):
        signal["news_scope"] = item["news_scope"]
    if item.get("sector_name"):
        signal["sector_name"] = item["sector_name"]
    if item.get("industry_name"):
        signal["industry_name"] = item["industry_name"]
    if item.get("company_name"):
        signal["company_name"] = item["company_name"]
    return signal


def _build_view(
    *,
    layer: str,
    items: list[dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    return {
        "layer": layer,
        "label": label,
        "count": len(items),
        "agent_conclusions": [_serialize_signal(item, layer=layer) for item in items],
    }


def _upsert_supporting_article(
    articles_by_id: dict[int, dict[str, Any]],
    item: dict[str, Any],
    *,
    layer: str,
    include_agent_signal: bool,
) -> None:
    article_id = int(item["article_id"])
    entry = articles_by_id.setdefault(
        article_id,
        {
            "article_id": article_id,
            "title": item.get("title") or "",
            "summary": item.get("summary") or "",
            "body": item.get("body") or "",
            "source": item.get("source") or "",
            "source_url": item.get("source_url") or "",
            "published_at": item.get("published_at") or "",
            "article_scope": item.get("article_scope") or "",
            "evidence_layers": [],
            "agent_signals": [],
        },
    )

    if layer not in entry["evidence_layers"]:
        entry["evidence_layers"].append(layer)

    if include_agent_signal:
        entry["agent_signals"].append(_serialize_signal(item, layer=layer))

    if not entry.get("summary") and item.get("summary"):
        entry["summary"] = item["summary"]
    if not entry.get("body") and item.get("body"):
        entry["body"] = item["body"]
    if not entry.get("title") and item.get("title"):
        entry["title"] = item["title"]


def _build_supporting_articles(
    evidence: dict[str, Any],
    *,
    summary_article_limit: int,
    full_article_limit: int,
) -> SupportingArticlesPayload:
    articles_by_id: dict[int, dict[str, Any]] = {}

    for item in evidence["macro_impacts"]:
        _upsert_supporting_article(articles_by_id, item, layer="macro_view", include_agent_signal=True)
    for item in evidence["sector_impacts"]:
        _upsert_supporting_article(articles_by_id, item, layer="sector_view", include_agent_signal=True)
    for item in evidence["industry_impacts"]:
        _upsert_supporting_article(articles_by_id, item, layer="industry_view", include_agent_signal=True)
    for item in evidence["company_impacts"]:
        _upsert_supporting_article(articles_by_id, item, layer="company_view", include_agent_signal=True)
    for item in evidence["sector_rss_articles"]:
        _upsert_supporting_article(articles_by_id, item, layer="sector_rss_evidence", include_agent_signal=False)

    def article_priority(article: dict[str, Any]) -> tuple[int, datetime, int]:
        high_confidence_signals = sum(
            1 for signal in article["agent_signals"] if str(signal.get("confidence") or "").lower() == HIGH_CONFIDENCE
        )
        signal_count = len(article["agent_signals"])
        layer_count = len(article["evidence_layers"])
        published_at = parse_published_at(article.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc)
        score = (high_confidence_signals * 100) + (signal_count * 10) + layer_count
        return score, published_at, int(article["article_id"])

    ranked_articles = sorted(
        articles_by_id.values(),
        key=article_priority,
        reverse=True,
    )

    summary_articles: list[SupportingArticleSummaryPayload] = [
        {
            "article_id": article["article_id"],
            "title": article["title"],
            "summary": article["summary"],
            "source": article["source"],
            "source_url": article["source_url"],
            "published_at": article["published_at"],
            "article_scope": article["article_scope"],
            "evidence_layers": article["evidence_layers"],
            "agent_signals": article["agent_signals"],
        }
        for article in ranked_articles[: max(1, int(summary_article_limit))]
    ]

    full_articles: list[SupportingFullArticlePayload] = [
        {
            "article_id": article["article_id"],
            "title": article["title"],
            "summary": article["summary"],
            "body": article["body"] or article["summary"],
            "source": article["source"],
            "source_url": article["source_url"],
            "published_at": article["published_at"],
            "article_scope": article["article_scope"],
            "evidence_layers": article["evidence_layers"],
            "agent_signals": article["agent_signals"],
        }
        for article in ranked_articles
        if str(article.get("body") or "").strip()
    ][: max(1, int(full_article_limit))]

    return {
        "article_summaries": summary_articles,
        "full_articles": full_articles,
    }
