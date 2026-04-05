from __future__ import annotations

import re
from urllib.parse import urlsplit


CNBC_BLACKLISTED_PATH_FRAGMENTS = (
    "/investingclub/video/",
    "/pro/news/",
    "/pro/options-investing/",
    "/application/pro/",
)


def normalize_match_text(value: str | None) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
    return " ".join(cleaned.split())


def is_blacklisted_cnbc_link(href: str) -> bool:
    normalized_href = href.lower()
    return "cnbc.com" in normalized_href and any(
        fragment in normalized_href for fragment in CNBC_BLACKLISTED_PATH_FRAGMENTS
    )


def link_matches_variants(link: dict, variants: set[str]) -> bool:
    href = str(link.get("href") or "")
    normalized_text = normalize_match_text(link.get("text"))
    parsed_href = urlsplit(href)
    href_match_text = " ".join(
        part
        for part in (
            parsed_href.netloc,
            parsed_href.path,
        )
        if part
    )
    normalized_href = normalize_match_text(href_match_text)
    return any(variant in normalized_text or variant in normalized_href for variant in variants)
