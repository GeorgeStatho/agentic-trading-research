from __future__ import annotations

import json

from db_helpers import DATA_DIR
from discovery.rss import extract_recent_rss_urls, fetch_recent_rss_urls, fetch_rss_feed_xml


DEFAULT_RSS_MAX_AGE_DAYS = 3
RSS_FEED_MAP_PATH = DATA_DIR / "sector_rss_feeds.json"


def load_sector_rss_feed_map() -> dict[str, dict]:
    with RSS_FEED_MAP_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def get_sector_feed_urls(sector: dict) -> list[str]:
    feed_map = load_sector_rss_feed_map()
    sector_key = str(sector.get("sector_key") or "").strip().lower()
    sector_name = str(sector.get("name") or "").strip().lower()

    matching_urls: list[str] = []
    for rss_url, metadata in feed_map.items():
        mapped_sector_key = str(metadata.get("sector_key") or "").strip().lower()
        mapped_sector_name = str(metadata.get("sector_name") or "").strip().lower()
        if sector_key and mapped_sector_key == sector_key:
            matching_urls.append(rss_url)
            continue
        if sector_name and mapped_sector_name == sector_name:
            matching_urls.append(rss_url)

    return matching_urls


def get_recent_sector_feed_article_urls(
    sector: dict,
    *,
    max_age_days: int = DEFAULT_RSS_MAX_AGE_DAYS,
) -> list[str]:
    rss_feed_urls = get_sector_feed_urls(sector)
    article_urls: list[str] = []
    seen_urls: set[str] = set()

    for rss_feed_url in rss_feed_urls:
        feed_urls = fetch_recent_rss_urls(rss_feed_url, max_age_days=max_age_days)
        for url in feed_urls:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            article_urls.append(url)

    return article_urls
