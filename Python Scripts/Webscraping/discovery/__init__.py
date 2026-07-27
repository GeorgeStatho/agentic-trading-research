"""Adapters that discover article candidates without extracting their bodies."""

from .rss import extract_recent_rss_urls, fetch_rss_feed_xml

__all__ = ["extract_recent_rss_urls", "fetch_rss_feed_xml"]
