"""Failure-tolerant RSS discovery shared by sector and regional workflows."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import xml.etree.ElementTree as ET

from fetching.http_client import HttpClient, HttpFetchError


LOGGER = logging.getLogger("stock_scraper.rss_discovery")
RSS_HEADERS = {
    "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
}


def fetch_rss_feed_xml(rss_url: str, timeout_seconds: int = 20) -> str:
    client = HttpClient(timeout_seconds=timeout_seconds, max_retries=2, headers=RSS_HEADERS)
    return client.get_text(rss_url)


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


def extract_recent_rss_urls(rss_xml: str, max_age_days: int = 3) -> list[str]:
    """Return unique, dated RSS links; malformed feeds are safe empty results."""

    try:
        root = ET.fromstring(rss_xml)
    except ET.ParseError as exc:
        LOGGER.warning("Ignoring malformed RSS payload: %s", exc)
        return []

    now = datetime.now(timezone.utc)
    urls: list[str] = []
    seen_urls: set[str] = set()
    for item in root.findall("./channel/item"):
        link = str(item.findtext("link") or "").strip()
        if not link or link in seen_urls:
            continue
        published_at = _parse_rss_pub_date(item.findtext("pubDate") or "")
        if published_at is None:
            continue
        age_days = (now - published_at).total_seconds() / 86400
        # Keep a small clock-skew/future-dated feed item rather than silently
        # dropping it; the previous RSS implementation accepted these items.
        if age_days > max_age_days:
            continue
        seen_urls.add(link)
        urls.append(link)
    return urls


def fetch_recent_rss_urls(rss_url: str, *, max_age_days: int = 3) -> list[str]:
    """Fetch and parse one feed, containing failures to that feed."""

    try:
        return extract_recent_rss_urls(fetch_rss_feed_xml(rss_url), max_age_days=max_age_days)
    except HttpFetchError as exc:
        LOGGER.warning("Ignoring unavailable RSS feed %s: %s", rss_url, exc)
        return []
