from db_helpers.common import DB_PATH, DATA_DIR, get_connection
from db_helpers.market import initialize_market_database, list_companies_by_industry, load_sector_tree, load_sector_tree_from_json
from db_helpers.news import (
    add_company_news_article,
    add_industry_news_article,
    add_industry_opportunist_impact,
    add_sector_news_article,
    add_us_news_article,
    add_us_news_sector_impact,
    add_world_news_article,
    add_world_news_sector_impact,
    initialize_news_database,
    list_company_news_articles,
    list_industry_news_articles,
    load_macro_events,
    load_news_articles,
    mark_industry_opportunist_article_processed,
    mark_us_news_article_processed,
    mark_world_news_article_processed,
)
from db_helpers.queries import get_all_companies, get_all_industries, get_all_sectors
