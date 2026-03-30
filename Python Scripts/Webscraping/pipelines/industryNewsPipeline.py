from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:
    WEBSCRAPING_DIR = Path(__file__).resolve().parents[1]
    if str(WEBSCRAPING_DIR) not in sys.path:
        sys.path.append(str(WEBSCRAPING_DIR))

from pipelines._shared import (
    ArticleExtractionResult,
    MAX_ARTICLES_PER_SEARCH_PAGE,
    MAX_ARTICLE_AGE_DAYS,
    build_content_hash,
    clear_failed_url,
    compute_article_scores,
    crawl_article_pages,
    crawl_articles,
    fetch_existing_article_by_url,
    filter_article_links,
    get_log_file_path,
    get_max_article_age_days,
    get_scrape_logger,
    get_source_metadata,
    is_allowed_source,
    is_recent_article,
    normalize_title,
    normalize_url,
    record_failed_url,
    cast,
)
from listing_page_helper import extract_listing_article_links
from pipelines._constants import INDUSTRY_NAME_STOPWORDS
from pipelines._internal import is_blacklisted_cnbc_link, link_matches_variants, normalize_match_text
from pipelines.job_builder import build_industry_source_jobs, group_jobs_by_url, unique_job_urls

from db_helpers import add_industry_news_article, get_all_industries, initialize_news_database


LOGGER = get_scrape_logger("industry_pipeline")
__all__ = ["get_all_industry_news"]


def _build_industry_match_variants(industry: dict) -> set[str]:
    industry_name = normalize_match_text(industry.get("name"))
    if not industry_name:
        return set()

    variants: set[str] = {industry_name}
    tokens = [token for token in industry_name.split() if token not in INDUSTRY_NAME_STOPWORDS]

    if tokens:
        variants.add(" ".join(tokens))

    if len(tokens) >= 2:
        variants.update(tokens)
        variants.update({" ".join(tokens[:2]), " ".join(tokens[-2:])})

    industry_key = normalize_match_text(industry.get("industry_key"))
    if industry_key:
        variants.add(industry_key)
        key_tokens = [token for token in industry_key.split() if token not in INDUSTRY_NAME_STOPWORDS]
        if key_tokens:
            variants.add(" ".join(key_tokens))
            variants.update(key_tokens)

    return {variant for variant in variants if len(variant) >= 4}


def _filter_industry_candidate_links(page_url: str, links: list[dict], industry: dict) -> list[dict]:
    base_candidates = filter_article_links(page_url, links)
    if not base_candidates:
        return []

    variants = _build_industry_match_variants(industry)
    if not variants:
        return []

    filtered: list[dict] = []
    for link in base_candidates:
        href = str(link.get("href") or "")
        if not href:
            continue
        if is_blacklisted_cnbc_link(href):
            LOGGER.info("Skipping blacklisted CNBC URL %s for industry %s", href, industry["name"])
            continue

        if link_matches_variants(link, variants):
            filtered.append(link)
            continue

        LOGGER.info(
            "Skipping weak industry match URL %s for industry %s because the link text did not match the industry name",
            href,
            industry["name"],
        )

    return filtered


def _save_followed_article_links(
    source_page_url: str,
    candidate_links: list[dict],
    industry: dict,
    max_articles: int = MAX_ARTICLES_PER_SEARCH_PAGE,
    max_age_days: int = MAX_ARTICLE_AGE_DAYS,
) -> int:
    saved_count = 0
    fetched_articles: dict[str, ArticleExtractionResult] = {}
    # Follow each filtered industry link one level deep, reusing existing
    # articles when possible and only saving recent articles to the DB.
    LOGGER.info(
        "Processing %s candidate article links for industry %s from %s",
        len(candidate_links),
        industry["name"],
        source_page_url,
    )

    urls_to_fetch: list[str] = []
    for link in candidate_links:
        if len(urls_to_fetch) >= max_articles:
            break

        href = link.get("href")
        if not href or fetch_existing_article_by_url(href) is not None:
            continue
        if not is_allowed_source(href):
            continue
        urls_to_fetch.append(href)

    if urls_to_fetch:
        LOGGER.info(
            "Fetching %s article pages through Scrapy for industry %s",
            len(urls_to_fetch),
            industry["name"],
        )
        fetched_articles = crawl_article_pages(urls_to_fetch)

    for link in candidate_links:
        if saved_count >= max_articles:
            LOGGER.info(
                "Reached article limit of %s for industry %s from %s",
                max_articles,
                industry["name"],
                source_page_url,
            )
            break

        href = link.get("href")
        if not href:
            continue
        if not is_allowed_source(href):
            LOGGER.info("Skipping disallowed article URL %s for industry %s", href, industry["name"])
            continue

        article_max_age_days = get_max_article_age_days(href, max_age_days)
        normalized_href = normalize_url(href)
        existing_article = fetch_existing_article_by_url(href)

        if existing_article is not None:
            existing_published_at = existing_article.get("published_at")
            if existing_published_at is not None and not isinstance(existing_published_at, str):
                existing_published_at = str(existing_published_at)
            if not is_recent_article(existing_published_at, max_age_days=article_max_age_days):
                LOGGER.info("Skipping stale existing article %s for industry %s", href, industry["name"])
                continue

            title = existing_article.get("title") or link.get("text") or href
            article_key = existing_article.get("article_key") or normalized_href or href
            source_metadata = get_source_metadata(existing_article.get("source_url") or href)
            source = existing_article.get("source") or source_metadata["domain"]
            normalized_article_title = existing_article.get("normalized_title") or normalize_title(title)
            content_hash = existing_article.get("content_hash") or build_content_hash(existing_article.get("body") or "")
            score_bundle = {
                "age_days": existing_article.get("age_days"),
                "recency_score": existing_article.get("recency_score"),
                "source_reputation_score": existing_article.get("source_reputation_score"),
                "directness_score": existing_article.get("directness_score"),
                "confirmation_score": existing_article.get("confirmation_score"),
                "independent_source_count": existing_article.get("independent_source_count"),
                "factuality_score": existing_article.get("factuality_score"),
                "evidence_score": existing_article.get("evidence_score"),
            }
            body = existing_article.get("body")
            published_at = existing_published_at
            extracted_article_payload = {
                "url": existing_article.get("source_url") or href,
                "title": existing_article.get("title"),
                "text": existing_article.get("body"),
                "published_at": existing_published_at,
                "success": True,
                "error": "",
                "reused_existing_article": True,
            }
            LOGGER.info("Reusing existing article %s for industry %s", href, industry["name"])
        else:
            article = cast(ArticleExtractionResult | None, fetched_articles.get(href))
            if article is None:
                LOGGER.warning(
                    "Missing crawled article result for industry %s at %s",
                    industry["name"],
                    href,
                )
                continue
            if not article.success:
                record_failed_url(href, "article_follow", article.error)
                LOGGER.warning(
                    "Article follow failed for industry %s at %s: %s",
                    industry["name"],
                    href,
                    article.error,
                )
                continue
            clear_failed_url(href)
            if not is_recent_article(article.published_at, max_age_days=article_max_age_days):
                LOGGER.info("Skipping stale fetched article %s for industry %s", href, industry["name"])
                continue

            title = article.title or link.get("text") or href
            article_key = normalized_href or href
            source_metadata = get_source_metadata(href)
            source = source_metadata["domain"]
            normalized_article_title = normalize_title(title)
            content_hash = build_content_hash(article.text)
            score_bundle = compute_article_scores(article, link, href, source_metadata)
            body = article.text
            published_at = article.published_at or None
            extracted_article_payload = {
                "url": article.url,
                "title": article.title,
                "text": article.text,
                "published_at": article.published_at,
                "success": article.success,
                "error": article.error,
                "reused_existing_article": False,
            }
            LOGGER.info("Fetched new article %s for industry %s", href, industry["name"])

        add_industry_news_article(
            industry_id=industry["id"],
            source=source,
            article_key=article_key,
            title=title,
            source_url=href,
            source_page_url=source_page_url,
            summary=link.get("text"),
            body=body,
            published_at=published_at,
            age_days=score_bundle.get("age_days"),
            recency_score=score_bundle.get("recency_score"),
            source_reputation_score=score_bundle.get("source_reputation_score"),
            directness_score=score_bundle.get("directness_score"),
            confirmation_score=score_bundle.get("confirmation_score"),
            independent_source_count=score_bundle.get("independent_source_count"),
            factuality_score=score_bundle.get("factuality_score"),
            evidence_score=score_bundle.get("evidence_score"),
            raw_json={
                "industry_id": industry["id"],
                "industry_key": industry["industry_key"],
                "industry_name": industry["name"],
                "source_page_url": source_page_url,
                "link": link,
                "normalized_url": normalized_href,
                "normalized_title": normalized_article_title,
                "content_hash": content_hash,
                "source_metadata": source_metadata,
                "scores": score_bundle,
                "extracted_article": extracted_article_payload,
            },
        )
        saved_count += 1
        LOGGER.info(
            "Saved article %s for industry %s (%s/%s saved for this page)",
            href,
            industry["name"],
            saved_count,
            max_articles,
        )

    return saved_count


def _process_source_page(page: dict, industry: dict, source_type: str) -> int:
    if not page.get("success"):
        LOGGER.warning(
            "Source page crawl failed for industry %s at %s: %s",
            industry["name"],
            page.get("url"),
            page.get("error"),
        )
        return 0
    if not supports_source_type(page["url"], source_type):
        return 0

    # Listing pages and search pages are filtered differently, but both feed
    # into the same article-follow stage after candidate URLs are chosen.
    if source_type == "listing":
        candidate_links = extract_listing_article_links(page["url"], page["links"], industry["name"])
    else:
        candidate_links = _filter_industry_candidate_links(page["url"], page["links"], industry)

    return _save_followed_article_links(page["url"], candidate_links, industry)


def _process_crawled_pages(crawled_pages: list[dict], jobs_by_url: dict[str, list[dict]]) -> dict[int, dict[str, int]]:
    from collections import defaultdict

    saved_counts: dict[int, dict[str, int]] = defaultdict(lambda: {"listing": 0, "search": 0})

    # Replay each crawled source page through the jobs that requested it and
    # track saved counts separately for listing-page and search-page sources.
    for page in crawled_pages:
        for job in jobs_by_url.get(page["url"], []):
            saved = _process_source_page(page, job["industry"], job["source_type"])
            saved_counts[job["industry"]["id"]][job["source_type"]] += saved

    return saved_counts


def get_all_industry_news() -> None:
    initialize_news_database()
    industries = get_all_industries()
    # Crawl all industry source pages in a single batch, then process listing
    # pages first and search pages second from the normalized crawl results.
    LOGGER.info("Starting all-industry scrape for %s industries", len(industries))
    jobs = build_industry_source_jobs(industries)
    jobs_by_url = group_jobs_by_url(jobs)
    urls = unique_job_urls(jobs)
    crawled_pages = crawl_articles(urls)
    saved_counts = _process_crawled_pages(crawled_pages, jobs_by_url)

    for industry in industries:
        industry_counts = saved_counts.get(industry["id"], {"listing": 0, "search": 0})
        listing_saved = industry_counts["listing"]
        print(f"Saved {listing_saved} listing-page articles for {industry['name']}")

        search_saved = industry_counts["search"]
        print(f"Saved {search_saved} search-page articles for {industry['name']}")
    LOGGER.info("Finished all-industry scrape. Log file: %s", get_log_file_path())
    print(f"Scrape log written to {get_log_file_path()}")


if __name__ == "__main__":
    get_all_industry_news()
