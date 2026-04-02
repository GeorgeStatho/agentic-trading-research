from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:
    WEBSCRAPING_DIR = Path(__file__).resolve().parents[1]
    if str(WEBSCRAPING_DIR) not in sys.path:
        sys.path.append(str(WEBSCRAPING_DIR))

from pipelines._regionNewsPipeline import DEFAULT_RSS_MAX_AGE_DAYS, make_region_pipeline
from pipelines._shared import get_log_file_path

from db_helpers import add_us_news_article, initialize_news_database


US_RSS_FEED_URL = "https://www.cnbc.com/id/15837362/device/rss/rss.html"
_PIPELINE = make_region_pipeline(
    logger_name="us_pipeline",
    region_slug="us",
    region_label="U.S. News",
    rss_feed_url=US_RSS_FEED_URL,
    save_article=add_us_news_article,
)


def get_us_news(urls: list[str]) -> int:
    initialize_news_database()
    return _PIPELINE["get_news"](urls)


def get_us_news_from_rss(max_age_days: int = DEFAULT_RSS_MAX_AGE_DAYS) -> int:
    initialize_news_database()
    return _PIPELINE["get_news_from_rss"](max_age_days=max_age_days)

__all__ = [
    "DEFAULT_RSS_MAX_AGE_DAYS",
    "US_RSS_FEED_URL",
    "get_us_news",
    "get_us_news_from_rss",
]


if __name__ == "__main__":
    try:
        initialize_news_database()
        get_us_news_from_rss()
    except KeyboardInterrupt:
        print(f"\nScrape interrupted by user. Log file: {get_log_file_path()}")
