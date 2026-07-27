"""Typed, deterministic source-policy registry.

This is the single authority for allow-listing, source capabilities, and
per-source metadata.  Compatibility helpers in ``source_config`` delegate here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit


CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "source.json"


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    domain: str
    allowed: bool
    reputation_score: float
    source_type: str
    paywalled: bool
    supports_search: bool
    supports_listing: bool
    supports_article: bool
    article_patterns: tuple[str, ...]
    max_article_age_days: int | None

    def supports(self, source_type: str) -> bool:
        if not self.allowed:
            return False
        if source_type == "search":
            return self.supports_search
        if source_type == "listing":
            return self.supports_listing
        if source_type == "article":
            return self.supports_article
        return False

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reputation_score": self.reputation_score,
            "source_type": self.source_type,
            "paywalled": self.paywalled,
            "listing_supported": self.supports_listing,
            "search_supported": self.supports_search,
            "article_patterns": list(self.article_patterns),
            "max_article_age_days": self.max_article_age_days,
            "domain": self.domain,
        }


def _policy_from_metadata(domain: str, metadata: dict) -> SourcePolicy:
    declared_supports = metadata.get("supports")
    supports = declared_supports if isinstance(declared_supports, dict) else None
    return SourcePolicy(
        domain=domain,
        allowed=bool(metadata.get("allowed")),
        reputation_score=float(metadata.get("reputation_score") or 0.0),
        source_type=str(metadata.get("source_type") or "unknown"),
        paywalled=bool(metadata.get("paywalled")),
        supports_search=bool(supports.get("search", False)) if supports is not None else bool(metadata.get("search_supported", False)),
        supports_listing=bool(supports.get("listing", False)) if supports is not None else bool(metadata.get("listing_supported", False)),
        supports_article=bool(supports.get("article", False)) if supports is not None else bool(metadata.get("article_patterns")),
        article_patterns=tuple(str(item) for item in (metadata.get("article_patterns") or [])),
        max_article_age_days=(
            int(metadata["max_article_age_days"])
            if metadata.get("max_article_age_days") is not None
            else None
        ),
    )


@lru_cache(maxsize=1)
def load_source_policies() -> tuple[SourcePolicy, ...]:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    defaults = dict(config.get("defaults") or config.get("default") or {})
    domains = config.get("domains") or config.get("sources") or {}
    policies = [_policy_from_metadata("", defaults)]
    policies.extend(
        _policy_from_metadata(str(domain).lower(), metadata)
        for domain, metadata in domains.items()
    )
    # Most-specific domain must win: markets.example.com before example.com.
    return tuple(sorted(policies, key=lambda policy: len(policy.domain), reverse=True))


def resolve_source_policy(url: str) -> SourcePolicy:
    hostname = (urlsplit(url).hostname or "").lower()
    for policy in load_source_policies():
        if policy.domain and (hostname == policy.domain or hostname.endswith(f".{policy.domain}")):
            return policy
    return next(policy for policy in load_source_policies() if not policy.domain)
