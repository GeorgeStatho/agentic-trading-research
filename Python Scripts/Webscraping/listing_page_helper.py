from __future__ import annotations

from source_config import get_article_patterns, is_allowed_source, supports_source_type


def extract_listing_article_links(page_url: str, links: list[dict], keyword: str) -> list[dict]:
    if not supports_source_type(page_url, "listing"):
        return []

    keyword_lower = keyword.lower().strip()
    patterns = get_article_patterns(page_url)

    filtered_links: list[dict] = []
    seen_hrefs: set[str] = set()
    for link in links:
        href = link.get("href", "")
        text = (link.get("text") or "").strip()
        if not href or not text or href in seen_hrefs:
            continue
        if not is_allowed_source(href):
            continue
        if patterns and not any(pattern in href for pattern in patterns):
            continue
        if keyword_lower and keyword_lower not in text.lower():
            continue

        filtered_links.append(
            {
                "href": href,
                "text": text,
            }
        )
        seen_hrefs.add(href)

    return filtered_links
