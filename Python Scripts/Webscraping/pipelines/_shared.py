from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import sys
from typing import cast


WEBSCRAPING_DIR = Path(__file__).resolve().parents[1]
if str(WEBSCRAPING_DIR) not in sys.path:
    sys.path.append(str(WEBSCRAPING_DIR))

DATA_DIR = Path(__file__).resolve().parents[2] / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

from core.CommonPipeline import (
    MAX_ARTICLES_PER_SEARCH_PAGE,
    MAX_ARTICLE_AGE_DAYS,
    build_source_url,
    clear_failed_url,
    compute_article_scores,
    fetch_existing_article_by_url,
    filter_article_links,
    is_recent_article,
    record_failed_url,
)
from core.scrape_logging import get_log_file_path, get_scrape_logger
from news_normalization import build_content_hash, normalize_title, normalize_url
from Normalization import ArticleExtractionResult, crawl_article_pages, crawl_articles
from source_config import (
    get_max_article_age_days,
    get_source_metadata,
    is_allowed_source,
    supports_source_type,
)
