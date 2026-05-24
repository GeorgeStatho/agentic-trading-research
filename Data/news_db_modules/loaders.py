from __future__ import annotations

from pathlib import Path
from typing import Any

from db_common import DB_PATH, clean_text, get_connection
from .article_store import add_news_article
from .bootstrap import initialize_database


def load_news_articles(
    articles: list[dict[str, Any]],
    db_path: Path | str = DB_PATH,
) -> None:
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        for article in articles:
            source = clean_text(article.get("source")) or "unknown"
            article_key = clean_text(article.get("article_key")) or clean_text(article.get("source_url"))
            title = clean_text(article.get("title")) or article_key
            source_url = clean_text(article.get("source_url"))
            if article_key is None or title is None or source_url is None:
                continue

            add_news_article(
                source=source,
                article_key=article_key,
                title=title,
                source_url=source_url,
                summary=clean_text(article.get("summary")),
                body=clean_text(article.get("body")),
                published_at=clean_text(article.get("published_at")),
                section=clean_text(article.get("section")),
                raw_json=article,
                conn=conn,
            )
