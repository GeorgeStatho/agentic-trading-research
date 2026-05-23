from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalize_title(title: str | None) -> str:
    cleaned = re.sub(r"\s+", " ", (title or "").strip().lower())
    return cleaned


def normalize_url(url: str | None) -> str:
    if not url:
        return ""

    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    path = re.sub(r"/+", "/", parts.path or "/").rstrip("/") or "/"
    filtered_query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not k.lower().startswith("utm_")]
    query = urlencode(filtered_query)
    return urlunsplit((scheme, netloc, path, query, ""))


def build_content_hash(text: str | None) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""

