from __future__ import annotations

from urllib.parse import urlsplit

from sources.registry import resolve_source_policy


def is_allowed_source(url: str) -> bool:
    return resolve_source_policy(url).allowed


def supports_source_type(url: str, source_type: str) -> bool:
    return resolve_source_policy(url).supports(source_type)


def get_article_patterns(url: str) -> list[str]:
    return list(resolve_source_policy(url).article_patterns)


def get_max_article_age_days(url: str, default_days: int) -> int:
    policy = resolve_source_policy(url)
    return policy.max_article_age_days or default_days


def get_source_metadata(url: str) -> dict:
    metadata = resolve_source_policy(url).as_dict()
    if not metadata["domain"]:
        metadata["domain"] = urlsplit(url).hostname or ""
    return metadata
