from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "source.json"


def _normalize_domain(value: str) -> str:
    lowered = value.lower().strip()
    if lowered.startswith("www."):
        return lowered[4:]
    return lowered


def _domain_from_url(url: str) -> str:
    return _normalize_domain(urlparse(url).netloc)


@lru_cache(maxsize=1)
def load_source_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def get_domain_config(domain_or_url: str) -> dict:
    config = load_source_config()
    defaults = dict(config.get("defaults", {}))
    domain = _domain_from_url(domain_or_url) if "://" in domain_or_url else _normalize_domain(domain_or_url)

    for candidate_domain, candidate_config in config.get("domains", {}).items():
        normalized_candidate = _normalize_domain(candidate_domain)
        if domain == normalized_candidate or domain.endswith(f".{normalized_candidate}"):
            merged = defaults
            merged.update(candidate_config)
            merged["domain"] = normalized_candidate
            return merged

    defaults["domain"] = domain
    return defaults


def is_allowed_source(domain_or_url: str) -> bool:
    return bool(get_domain_config(domain_or_url).get("allowed"))


def supports_source_type(source_url: str, source_type: str) -> bool:
    domain_config = get_domain_config(source_url)
    support_key = f"{source_type}_supported"
    return bool(domain_config.get("allowed")) and bool(domain_config.get(support_key))


def get_article_patterns(domain_or_url: str) -> list[str]:
    return list(get_domain_config(domain_or_url).get("article_patterns", []))


def get_max_article_age_days(domain_or_url: str, fallback_days: int) -> int:
    value = get_domain_config(domain_or_url).get("max_article_age_days")
    if isinstance(value, int) and value > 0:
        return value
    return fallback_days


def get_source_metadata(domain_or_url: str) -> dict:
    return get_domain_config(domain_or_url)
