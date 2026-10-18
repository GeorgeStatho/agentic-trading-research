from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

from core.scrape_logging import get_scrape_logger
from db_common import DATA_DIR
from engine.page_fetch import fetch_feed


LOGGER = get_scrape_logger("sector_rss")
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


def _parse_rss_pub_date(pub_date: str) -> datetime | None:
    value = str(pub_date or "").strip()
    if not value:
        return None

    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def extract_recent_rss_items(
    rss_xml: str | None,
    max_age_days: int = DEFAULT_RSS_MAX_AGE_DAYS,
) -> list[tuple[str, str | None]]:
    # Extract the recent (link, description) pairs from the raw response and return a stable value.
    if not rss_xml:
        return []
    try:
        root = ET.fromstring(rss_xml)
    except ET.ParseError as exc:
        LOGGER.warning("Skipping unparseable RSS feed: %s", exc)
        return []
    now = datetime.now(timezone.utc)
    items: list[tuple[str, str | None]] = []

    for item in root.findall("./channel/item"):
        link = str(item.findtext("link") or "").strip()
        if not link:
            continue

        published_at = _parse_rss_pub_date(item.findtext("pubDate") or "")
        if published_at is None:
            continue

        age_days = (now - published_at).total_seconds() / 86400
        if age_days > max_age_days:
            continue

        description = BeautifulSoup(item.findtext("description") or "", "html.parser").get_text(" ", strip=True)
        items.append((link, description or None))

    return items


def get_recent_sector_feed_items(
    sector: dict,
    *,
    max_age_days: int = DEFAULT_RSS_MAX_AGE_DAYS,
) -> list[tuple[str, str | None]]:
    rss_feed_urls = get_sector_feed_urls(sector)
    feed_items: list[tuple[str, str | None]] = []
    seen_urls: set[str] = set()

    for rss_feed_url in rss_feed_urls:
        for url, description in extract_recent_rss_items(fetch_feed(rss_feed_url), max_age_days=max_age_days):
            if url in seen_urls:
                continue
            seen_urls.add(url)
            feed_items.append((url, description))

    return feed_items
