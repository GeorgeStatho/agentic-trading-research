from __future__ import annotations

from source_config import get_article_patterns, is_allowed_source, supports_source_type


def extract_listing_article_links(page_url: str, links: list[dict], topic_text: str) -> list[dict]:
    if not is_allowed_source(page_url) or not supports_source_type(page_url, "listing"):
        return []

    patterns = get_article_patterns(page_url)
    normalized_topic = " ".join((topic_text or "").lower().split())

    results: list[dict] = []
    seen_hrefs: set[str] = set()
    for link in links:
        href = str(link.get("href") or "").strip()
        text = " ".join(str(link.get("text") or "").lower().split())
        if not href or href in seen_hrefs:
            continue
        if not is_allowed_source(href):
            continue
        if patterns and not any(pattern in href for pattern in patterns):
            continue
        if normalized_topic and normalized_topic not in text and normalized_topic not in href.lower():
            continue

        results.append(link)
        seen_hrefs.add(href)

    return results

