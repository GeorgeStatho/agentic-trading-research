from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit


CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "source.json"


@lru_cache(maxsize=1)
def _load_source_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _default_metadata(config: dict) -> dict:
    # Support both the older singular keys and the current config schema.
    return dict(config.get("defaults") or config.get("default") or {})


def _match_domain(url: str) -> tuple[str | None, dict]:
    config = _load_source_config()
    sources = config.get("domains") or config.get("sources") or {}
    hostname = (urlsplit(url).hostname or "").lower()
    for domain, metadata in sources.items():
        normalized_domain = domain.lower()
        if hostname == normalized_domain or hostname.endswith(f".{normalized_domain}"):
            return domain, metadata
    return None, _default_metadata(config)


def is_allowed_source(url: str) -> bool:
    _, metadata = _match_domain(url)
    return bool(metadata.get("allowed"))


def supports_source_type(url: str, source_type: str) -> bool:
    _, metadata = _match_domain(url)
    supports = metadata.get("supports")
    if isinstance(supports, dict):
        return bool(supports.get(source_type, False))

    if source_type == "search":
        return bool(metadata.get("search_supported", False))
    if source_type == "listing":
        return bool(metadata.get("listing_supported", False))
    return False


def get_article_patterns(url: str) -> list[str]:
    _, metadata = _match_domain(url)
    return list(metadata.get("article_patterns") or [])


def get_max_article_age_days(url: str, default_days: int) -> int:
    _, metadata = _match_domain(url)
    return int(metadata.get("max_article_age_days") or default_days)


def get_source_metadata(url: str) -> dict:
    domain, metadata = _match_domain(url)
    resolved = dict(metadata)
    resolved.setdefault("domain", domain or (urlsplit(url).hostname or ""))
    resolved.setdefault("reputation_score", 0.0)
    resolved.setdefault("source_type", "unknown")
    resolved.setdefault("paywalled", False)
    return resolved
