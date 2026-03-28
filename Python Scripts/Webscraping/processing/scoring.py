from __future__ import annotations

import math
import re

from source_config import get_source_metadata


DIRECTNESS_POSITIVE_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bpress release\b",
        r"\bearnings call\b",
        r"\bconference call\b",
        r"\bsec filing\b",
        r"\b10-k\b",
        r"\b10-q\b",
        r"\b8-k\b",
        r"\bthe company said\b",
        r"\baccording to (a|the) filing\b",
    ]
]
DIRECTNESS_NEGATIVE_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bopinion\b",
        r"\banalysis\b",
        r"\beditorial\b",
        r"\bcommentary\b",
    ]
]
FACTUAL_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\breported\b",
        r"\bannounced\b",
        r"\bfiled\b",
        r"\bsaid\b",
        r"\baccording to\b",
        r"\bresults\b",
    ]
]
SPECULATIVE_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in [
        r"\bcould\b",
        r"\bmight\b",
        r"\bmay\b",
        r"\blikely\b",
        r"\bappears?\b",
        r"\banalysts believe\b",
        r"\bexpected to\b",
    ]
]


def _score_window(title: str | None, body: str | None, summary: str | None, max_body_chars: int = 1400) -> str:
    title_text = (title or "").strip()
    summary_text = (summary or "").strip()
    body_text = (body or "").strip()[:max_body_chars]
    return "\n".join(part for part in (title_text, summary_text, body_text) if part)


def recency_score(age_days: float | None, half_life_days: float = 7.0) -> float:
    if age_days is None:
        return 0.0
    return math.exp(-math.log(2) * max(age_days, 0.0) / half_life_days)


def confirmation_score(independent_sources: int, k: float = 0.7) -> float:
    return 1.0 - math.exp(-k * max(independent_sources, 0))


def compute_directness_score(
    title: str | None,
    body: str | None,
    summary: str | None,
    source_url: str,
    source_metadata: dict | None = None,
) -> float:
    text = _score_window(title, body, summary)
    metadata = source_metadata or get_source_metadata(source_url)
    base = 0.45

    source_type = str(metadata.get("source_type") or "")
    if source_type in {"wire", "government", "company_ir"}:
        base = 0.7
    elif "opinion" in source_type or "analysis" in source_type:
        base = 0.35

    positive_hits = sum(1 for pattern in DIRECTNESS_POSITIVE_PATTERNS if pattern.search(text))
    negative_hits = sum(1 for pattern in DIRECTNESS_NEGATIVE_PATTERNS if pattern.search(text))
    score = base + 0.12 * positive_hits - 0.12 * negative_hits
    return max(0.0, min(1.0, score))


def compute_factuality_score(
    title: str | None,
    body: str | None,
    summary: str | None,
    source_url: str,
    source_metadata: dict | None = None,
) -> float:
    text = _score_window(title, body, summary)
    metadata = source_metadata or get_source_metadata(source_url)
    base = float(metadata.get("reputation_score") or 0.5)

    factual_hits = sum(1 for pattern in FACTUAL_PATTERNS if pattern.search(text))
    speculative_hits = sum(1 for pattern in SPECULATIVE_PATTERNS if pattern.search(text))
    score = base + 0.06 * factual_hits - 0.08 * speculative_hits
    return max(0.0, min(1.0, score))


def compute_evidence_score(
    recency: float,
    source_reputation: float,
    directness: float,
    confirmation: float,
    factuality: float,
) -> float:
    score = (
        0.30 * recency
        + 0.25 * source_reputation
        + 0.20 * directness
        + 0.15 * confirmation
        + 0.10 * factuality
    )
    return max(0.0, min(1.0, score))

