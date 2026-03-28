from __future__ import annotations

from urllib.parse import urlparse


LISTING_PATTERNS_BY_DOMAIN = {
    "investing.com": ["/news/"],
    "morningstar.com": ["/news/", "/markets/", "/stocks/"],
}


def extract_listing_article_links(page_url: str, links: list[dict], keyword: str) -> list[dict]:
    domain = urlparse(page_url).netloc.lower()
    keyword_lower = keyword.lower().strip()

    patterns = []
    for candidate_domain, candidate_patterns in LISTING_PATTERNS_BY_DOMAIN.items():
        if candidate_domain in domain:
            patterns = candidate_patterns
            break

    filtered_links: list[dict] = []
    seen_hrefs: set[str] = set()
    for link in links:
        href = link.get("href", "")
        text = (link.get("text") or "").strip()
        if not href or not text or href in seen_hrefs:
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
