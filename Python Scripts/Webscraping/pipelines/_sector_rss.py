from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from db_helpers import DATA_DIR


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


def fetch_rss_feed_xml(rss_url: str, timeout_seconds: int = 20) -> str:
    request = Request(
        rss_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; StockTradingExperiment/1.0; +https://www.cnbc.com/)",
            "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8", errors="replace")


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


def extract_recent_rss_urls(rss_xml: str, max_age_days: int = DEFAULT_RSS_MAX_AGE_DAYS) -> list[str]:
    root = ET.fromstring(rss_xml)
    now = datetime.now(timezone.utc)
    urls: list[str] = []

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

        urls.append(link)

    return urls


def get_recent_sector_feed_article_urls(
    sector: dict,
    *,
    max_age_days: int = DEFAULT_RSS_MAX_AGE_DAYS,
) -> list[str]:
    rss_feed_urls = get_sector_feed_urls(sector)
    article_urls: list[str] = []
    seen_urls: set[str] = set()

    for rss_feed_url in rss_feed_urls:
        rss_xml = fetch_rss_feed_xml(rss_feed_url)
        feed_urls = extract_recent_rss_urls(rss_xml, max_age_days=max_age_days)
        for url in feed_urls:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            article_urls.append(url)

    return article_urls
