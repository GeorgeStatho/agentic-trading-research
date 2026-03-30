from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:
    WEBSCRAPING_DIR = Path(__file__).resolve().parents[1]
    if str(WEBSCRAPING_DIR) not in sys.path:
        sys.path.append(str(WEBSCRAPING_DIR))

from pipelines._regionNewsPipeline import DEFAULT_RSS_MAX_AGE_DAYS, make_region_pipeline

from db_helpers import add_world_news_article, initialize_news_database


WORLD_RSS_FEED_URL = "https://www.cnbc.com/id/100727362/device/rss/rss.html"
_PIPELINE = make_region_pipeline(
    logger_name="world_pipeline",
    region_slug="world",
    region_label="World News",
    rss_feed_url=WORLD_RSS_FEED_URL,
    save_article=add_world_news_article,
)


def get_world_news(urls: list[str]) -> int:
    initialize_news_database()
    return _PIPELINE["get_news"](urls)


def get_world_news_from_rss(max_age_days: int = DEFAULT_RSS_MAX_AGE_DAYS) -> int:
    initialize_news_database()
    return _PIPELINE["get_news_from_rss"](max_age_days=max_age_days)

__all__ = [
    "DEFAULT_RSS_MAX_AGE_DAYS",
    "WORLD_RSS_FEED_URL",
    "get_world_news",
    "get_world_news_from_rss",
]


if __name__ == "__main__":
    initialize_news_database()
    get_world_news_from_rss()
