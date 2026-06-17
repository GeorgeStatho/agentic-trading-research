from __future__ import annotations

from db_common import DB_PATH
from news_db_modules.article_store import add_news_article
from news_db_modules.bootstrap import initialize_database
from news_db_modules.entity_articles import (
    add_company_news_article,
    add_industry_news_article,
    add_sector_news_article,
)
from news_db_modules.global_articles import add_us_news_article, add_world_news_article
from news_db_modules.impacts import (
    add_company_opportunist_impact,
    add_industry_opportunist_impact,
    add_sector_opportunist_impact,
    add_strategist_company_summary,
    add_us_news_sector_impact,
    add_world_news_sector_impact,
)
from news_db_modules.links import (
    link_company_to_article,
    link_industry_to_article,
    link_sector_to_article,
    link_us_article,
    link_world_article,
)
from news_db_modules.loaders import load_news_articles
from news_db_modules.macro import add_macro_event, load_macro_events
from news_db_modules.processing import (
    mark_company_opportunist_article_processed,
    mark_industry_opportunist_article_processed,
    mark_sector_opportunist_article_processed,
    mark_us_news_article_processed,
    mark_world_news_article_processed,
)
from news_db_modules.queries import (
    add_failed_url,
    get_failed_url,
    list_company_news_articles,
    list_industry_news_articles,
    remove_failed_url,
)

__all__ = [
    "DB_PATH",
    "add_company_news_article",
    "add_company_opportunist_impact",
    "add_failed_url",
    "add_industry_news_article",
    "add_industry_opportunist_impact",
    "add_macro_event",
    "add_news_article",
    "add_sector_news_article",
    "add_sector_opportunist_impact",
    "add_strategist_company_summary",
    "add_us_news_article",
    "add_us_news_sector_impact",
    "add_world_news_article",
    "add_world_news_sector_impact",
    "get_failed_url",
    "initialize_database",
    "link_company_to_article",
    "link_industry_to_article",
    "link_sector_to_article",
    "link_us_article",
    "link_world_article",
    "list_company_news_articles",
    "list_industry_news_articles",
    "load_macro_events",
    "load_news_articles",
    "mark_company_opportunist_article_processed",
    "mark_industry_opportunist_article_processed",
    "mark_sector_opportunist_article_processed",
    "mark_us_news_article_processed",
    "mark_world_news_article_processed",
    "remove_failed_url",
]


if __name__ == "__main__":
    initialize_database()
    print(f"Initialized news database schema in {DB_PATH}")
