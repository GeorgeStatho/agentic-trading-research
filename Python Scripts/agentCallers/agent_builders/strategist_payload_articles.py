from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent_contracts import (
    AgentSignalPayload,
    SupportingArticlesPayload,
    SupportingArticleSummaryPayload,
    SupportingFullArticlePayload,
)
from agent_helpers.text_utils import parse_published_at
from agent_helpers.strategist_evidence import HIGH_CONFIDENCE
import json

SUPPORTING_ARTICLE_SELECTION_VERSION = "company-first-quotas-v1"
# Most specific evidence wins; `unsignalled` (sector_rss only) has no quota and is backfill only.
ARTICLE_BUCKET_ORDER = ("company", "industry", "sector", "macro", "unsignalled")
SUMMARY_ARTICLE_QUOTAS = {"company": 10, "industry": 5, "sector": 3, "macro": 2}
SUMMARY_QUOTA_BASE_LIMIT = 20  # the limit the quotas above are written for


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
        "evidence_score": item.get("evidence_score"),
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
    for key in ('catalyst_id', 'catalyst_family', 'catalyst_type', 'mechanism', 'expected_horizon'):
        if item.get(key):
            signal[key] = item[key]
    if item.get('root_event_ids_json'):
        signal['root_event_ids'] = json.loads(item['root_event_ids_json'])
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
            "evidence_score": item.get("evidence_score"),
            "root_event_id": item.get("root_event_id"),
            "root_source_id": item.get("root_source_id"),
            "primary_ticker": item.get("primary_ticker"),
            "catalyst_id": item.get("catalyst_id"),
            "source_type": item.get("source_type"),
            "independent_source_count": item.get("independent_source_count"),

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

    symbol = str((evidence.get("company") or {}).get("symbol") or "").upper()

    def bucket_index(article: dict[str, Any]) -> int:
        layers = article["evidence_layers"]
        if "company_view" in layers or (symbol and str(article.get("primary_ticker") or "").upper() == symbol):
            return 0
        for index, layer in ((1, "industry_view"), (2, "sector_view"), (3, "macro_view")):
            if layer in layers:
                return index
        return 4

    # sort() is stable, so article_priority order survives within each bucket.
    ranked_articles = sorted(articles_by_id.values(), key=article_priority, reverse=True)
    ranked_articles.sort(key=bucket_index)

    limit = max(1, int(summary_article_limit))
    picked = []
    for index, name in enumerate(ARTICLE_BUCKET_ORDER):
        quota = SUMMARY_ARTICLE_QUOTAS.get(name, 0) * limit // SUMMARY_QUOTA_BASE_LIMIT
        picked += [a for a in ranked_articles if bucket_index(a) == index][:quota]
    picked_ids = {a["article_id"] for a in picked}
    picked += [a for a in ranked_articles if a["article_id"] not in picked_ids][: limit - len(picked)]

    summary_articles: list[SupportingArticleSummaryPayload] = [
        {
            "article_id": article["article_id"],
            "title": article["title"],
            "summary": article["summary"],
            "source": article["source"],
            "source_url": article["source_url"],
            "published_at": article["published_at"],
            "article_scope": article["article_scope"],
            "evidence_score": article.get("evidence_score"),
            "root_event_id": article.get("root_event_id"),
            "root_source_id": article.get("root_source_id"),
            "primary_ticker": article.get("primary_ticker"),
            "catalyst_id": article.get("catalyst_id"),
            "source_type": article.get("source_type"),
            "independent_source_count": article.get("independent_source_count"),

            "evidence_layers": article["evidence_layers"],
            "agent_signals": article["agent_signals"],
        }
        for article in picked
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
            "evidence_score": article.get("evidence_score"),
            "root_event_id": article.get("root_event_id"),
            "root_source_id": article.get("root_source_id"),
            "primary_ticker": article.get("primary_ticker"),
            "catalyst_id": article.get("catalyst_id"),
            "source_type": article.get("source_type"),
            "independent_source_count": article.get("independent_source_count"),

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
