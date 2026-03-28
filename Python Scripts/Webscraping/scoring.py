from __future__ import annotations

import math
import re
from typing import Any

from source_config import get_source_metadata


LEAD_TEXT_LIMIT = 1400

PRIMARY_SOURCE_PATTERNS = [
    r"\baccording to (?:an?|the) (?:sec )?filing\b",
    r"\b(?:8-k|10-k|10-q|s-1|form 4)\b",
    r"\bpress release\b",
    r"\bearnings (?:report|release|call)\b",
    r"\bthe company said\b",
    r"\bthe company announced\b",
    r"\bthe labor department said\b",
    r"\bthe federal reserve said\b",
    r"\bthe bureau of labor statistics said\b",
    r"\bmanagement said\b",
    r"\bceo said\b",
    r"\bcfo said\b",
    r"\bresults showed\b",
]

SECONDARY_SOURCE_PATTERNS = [
    r"\baccording to analysts\b",
    r"\bmarket participants\b",
    r"\binvestors said\b",
    r"\bstrategists said\b",
    r"\bcommentary\b",
    r"\bmarket wrap\b",
    r"\bopinion\b",
    r"\banalysis\b",
]

FACTUAL_PATTERNS = [
    r"\breported\b",
    r"\bannounced\b",
    r"\bfiled\b",
    r"\bsaid\b",
    r"\bstated\b",
    r"\baccording to\b",
    r"\bresults showed\b",
    r"\brevenue\b",
    r"\bearnings\b",
    r"\bguidance\b",
    r"\bsec\b",
    r"\bdepartment\b",
    r"\bministry\b",
    r"\bcentral bank\b",
    r"\b\d+(?:\.\d+)?%\b",
    r"\$\d+(?:\.\d+)?\b",
]

SPECULATIVE_PATTERNS = [
    r"\bcould\b",
    r"\bmight\b",
    r"\bmay\b",
    r"\blikely\b",
    r"\bunlikely\b",
    r"\bappears?\b",
    r"\bsuggests?\b",
    r"\bexpected to\b",
    r"\bwould\b",
    r"\bif\b",
    r"\bpotential(?:ly)?\b",
    r"\brumou?r\b",
    r"\bspeculat(?:e|ion|ive)\b",
]

URL_DIRECT_PATTERNS = [
    r"/press-release",
    r"/press-release/",
    r"/earnings",
    r"/sec-",
    r"/filing",
    r"/news/",
]

URL_OPINION_PATTERNS = [
    r"/opinion",
    r"/analysis",
    r"/commentary",
]

SOURCE_TYPE_DIRECTNESS_BASE = {
    "wire": 0.85,
    "financial_news": 0.7,
    "research_financial_news": 0.72,
    "business_news": 0.68,
    "market_news_portal": 0.55,
    "financial_news_aggregator": 0.5,
    "market_portal": 0.45,
    "analysis_opinion": 0.35,
    "unknown": 0.4,
}

SOURCE_TYPE_FACTUALITY_BASE = {
    "wire": 0.9,
    "financial_news": 0.78,
    "research_financial_news": 0.76,
    "business_news": 0.72,
    "market_news_portal": 0.6,
    "financial_news_aggregator": 0.52,
    "market_portal": 0.5,
    "analysis_opinion": 0.38,
    "unknown": 0.45,
}


def _clip(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _count_matches(patterns: list[str], text: str) -> int:
    return sum(len(re.findall(pattern, text, flags=re.IGNORECASE)) for pattern in patterns)


def _build_scoring_window(title: str | None, body: str | None, summary: str | None = None) -> str:
    title_text = (title or "").strip()
    summary_text = (summary or "").strip()
    body_text = " ".join((body or "").split())[:LEAD_TEXT_LIMIT]
    return "\n".join(part for part in [title_text, summary_text, body_text] if part)


def _source_type(metadata: dict[str, Any] | None, source_url: str | None) -> str:
    if metadata is not None:
        return str(metadata.get("source_type") or "unknown")
    if source_url:
        return str(get_source_metadata(source_url).get("source_type") or "unknown")
    return "unknown"


def recency_score(age_days: float, half_life_days: float = 7.0) -> float:
    return math.exp(-math.log(2) * age_days / half_life_days)


def confirmation_score(independent_sources: int, k: float = 0.7) -> float:
    return 1.0 - math.exp(-k * independent_sources)


def compute_directness_score(
    title: str | None = None,
    body: str | None = None,
    summary: str | None = None,
    source_url: str | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> float:
    text = _build_scoring_window(title, body, summary)
    source_type = _source_type(source_metadata, source_url)

    score = SOURCE_TYPE_DIRECTNESS_BASE.get(source_type, SOURCE_TYPE_DIRECTNESS_BASE["unknown"])
    primary_hits = _count_matches(PRIMARY_SOURCE_PATTERNS, text)
    secondary_hits = _count_matches(SECONDARY_SOURCE_PATTERNS, text)

    score += min(0.35, 0.12 * primary_hits)
    score -= min(0.25, 0.08 * secondary_hits)

    url_value = (source_url or "").lower()
    if _count_matches(URL_DIRECT_PATTERNS, url_value) > 0:
        score += 0.08
    if _count_matches(URL_OPINION_PATTERNS, url_value) > 0:
        score -= 0.15

    return _clip(score)


def compute_factuality_score(
    title: str | None = None,
    body: str | None = None,
    summary: str | None = None,
    source_url: str | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> float:
    text = _build_scoring_window(title, body, summary)
    source_type = _source_type(source_metadata, source_url)

    base_score = SOURCE_TYPE_FACTUALITY_BASE.get(source_type, SOURCE_TYPE_FACTUALITY_BASE["unknown"])
    factual_hits = _count_matches(FACTUAL_PATTERNS, text)
    speculative_hits = _count_matches(SPECULATIVE_PATTERNS, text)

    evidence_total = factual_hits + speculative_hits
    if evidence_total == 0:
        balance_score = 0.5
    else:
        balance_score = factual_hits / evidence_total

    number_bonus = 0.08 if re.search(r"\b\d+(?:\.\d+)?(?:%| billion| million| bps)?\b", text, flags=re.IGNORECASE) else 0.0
    url_value = (source_url or "").lower()
    opinion_penalty = 0.12 if _count_matches(URL_OPINION_PATTERNS, url_value) > 0 else 0.0

    score = 0.55 * base_score + 0.35 * balance_score + number_bonus - opinion_penalty
    if speculative_hits > factual_hits:
        score -= 0.08

    return _clip(score)


def compute_evidence_score(
    recency: float,
    source_reputation: float,
    directness: float,
    confirmation: float,
    factuality: float,
) -> float:
    return _clip(
        0.30 * recency
        + 0.25 * source_reputation
        + 0.20 * directness
        + 0.15 * confirmation
        + 0.10 * factuality
    )
