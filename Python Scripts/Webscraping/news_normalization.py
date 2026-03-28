from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl
from urllib.parse import urlencode
from urllib.parse import urlsplit
from urllib.parse import urlunsplit


TRACKING_QUERY_PREFIXES = (
    "utm_",
    "fbclid",
    "gclid",
    "guccounter",
    "guce_referrer",
    "guce_referrer_sig",
    "soc_src",
    "soc_trk",
)


def normalize_url(url: str) -> str:
    if not url:
        return ""

    parts = urlsplit(url.strip())
    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered_key = key.lower()
        if lowered_key.startswith(TRACKING_QUERY_PREFIXES) or lowered_key in TRACKING_QUERY_PREFIXES:
            continue
        query_items.append((key, value))

    normalized_query = urlencode(sorted(query_items))
    normalized_path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), normalized_path, normalized_query, ""))


def normalize_title(title: str) -> str:
    if not title:
        return ""

    normalized = title.lower()
    normalized = re.sub(r"\s*\|\s*[^|]+$", "", normalized)
    normalized = re.sub(r"\s*-\s*[^-]+$", "", normalized)
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def normalize_body_text(text: str) -> str:
    if not text:
        return ""

    normalized = text.lower()
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def build_content_hash(text: str) -> str:
    normalized_text = normalize_body_text(text)
    if not normalized_text:
        return ""
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
