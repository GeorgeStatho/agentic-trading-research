from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:
    WEBSCRAPING_DIR = Path(__file__).resolve().parents[1]
    if str(WEBSCRAPING_DIR) not in sys.path:
        sys.path.append(str(WEBSCRAPING_DIR))

from pipelines._shared import (
    MAX_ARTICLES_PER_SEARCH_PAGE,
    MAX_ARTICLE_AGE_DAYS,
    filter_article_links,
    get_log_file_path,
    get_scrape_logger,
    supports_source_type,
)
from listing_page_helper import extract_listing_article_links
from pipelines._constants import INDUSTRY_NAME_STOPWORDS
from pipelines._entity_adapters import (
    make_bucketed_count_accumulator,
    make_direct_request_builder,
    make_entity_article_saver,
    make_request_saver,
    make_search_request_builder,
)
from pipelines._internal import is_blacklisted_cnbc_link, link_matches_variants, normalize_match_text
from pipelines._orchestration import (
    run_mixed_job_orchestration,
)
from pipelines.job_builder import build_industry_source_jobs, group_jobs_by_url, unique_job_urls

from db_helpers import add_industry_news_article, get_all_industries, initialize_news_database


LOGGER = get_scrape_logger("industry_pipeline")
__all__ = ["get_all_industry_news", "get_industry_news", "list_supported_industries"]


def _expand_industry_variant_forms(value: str) -> set[str]:
    variants = {value}
    tokens = value.split()

    def _token_forms(token: str) -> set[str]:
        forms = {token}
        if len(token) > 4 and token.endswith("ies"):
            forms.add(token[:-3] + "y")
        if len(token) > 4 and token.endswith("es"):
            forms.add(token[:-2])
        if len(token) > 4 and token.endswith("s"):
            forms.add(token[:-1])
        return {form for form in forms if len(form) >= 4}

    if tokens:
        token_options = [_token_forms(token) for token in tokens]
        expanded_tokens = [sorted(options) for options in token_options]

        def _walk(index: int, current: list[str]) -> None:
            if index >= len(expanded_tokens):
                variants.add(" ".join(current))
                return
            for option in expanded_tokens[index]:
                _walk(index + 1, [*current, option])

        _walk(0, [])

    return {variant for variant in variants if len(variant) >= 4}


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

    expanded_variants: set[str] = set()
    for variant in variants:
        expanded_variants.update(_expand_industry_variant_forms(variant))

    return {variant for variant in expanded_variants if len(variant) >= 4}


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


_save_followed_article_links = make_entity_article_saver(
    logger=LOGGER,
    entity_kind="industry",
    entity_label=lambda industry: industry["name"],
    save_article=lambda *, entity, source_page_url, context, **extra: add_industry_news_article(
        industry_id=entity["id"],
        source=context["source"],
        article_key=context["article_key"],
        title=context["title"],
        source_url=context["href"],
        source_page_url=source_page_url,
        summary=context["link"].get("text"),
        body=context["body"],
        published_at=context["published_at"],
        age_days=context["score_bundle"].get("age_days"),
        recency_score=context["score_bundle"].get("recency_score"),
        source_reputation_score=context["score_bundle"].get("source_reputation_score"),
        directness_score=context["score_bundle"].get("directness_score"),
        confirmation_score=context["score_bundle"].get("confirmation_score"),
        independent_source_count=context["score_bundle"].get("independent_source_count"),
        factuality_score=context["score_bundle"].get("factuality_score"),
        evidence_score=context["score_bundle"].get("evidence_score"),
        raw_json=context["raw_json"],
    ),
    build_raw_json=lambda *, entity, source_page_url, context, **extra: {
        "industry_id": entity["id"],
        "industry_key": entity["industry_key"],
        "industry_name": entity["name"],
        "source_page_url": source_page_url,
        "link": context["link"],
        "normalized_url": context["normalized_href"],
        "normalized_title": context["normalized_article_title"],
        "content_hash": context["content_hash"],
        "source_metadata": context["source_metadata"],
        "scores": context["score_bundle"],
        "extracted_article": context["extracted_article_payload"],
    },
)


_build_industry_search_save_requests = make_search_request_builder(
    logger=LOGGER,
    should_process_page=lambda page: True,
    build_candidate_links=lambda page, job: (
        extract_listing_article_links(page["url"], page["links"], job["industry"]["name"])
        if job["source_type"] == "listing"
        else _filter_industry_candidate_links(page["url"], page["links"], job["industry"])
    ) if supports_source_type(page["url"], job["source_type"]) else [],
    build_request_payload=lambda job: {
        "industry": job["industry"],
        "source_type": job["source_type"],
    },
    entity_from_job=lambda job: job["industry"],
    max_articles=MAX_ARTICLES_PER_SEARCH_PAGE,
    failure_message=lambda page, job: (
        "Source page crawl failed for industry %s at %s: %s",
        (job["industry"]["name"], page.get("url"), page.get("error")),
    ),
)


_build_industry_direct_article_save_requests = make_direct_request_builder(
    entity_from_job=lambda job: job["industry"],
    text_from_job=lambda job: job.get("search_term") or job["industry"]["name"],
    build_request_payload=lambda job: {
        "industry": job["industry"],
        "source_type": job["source_type"],
    },
)


_save_industry_request = make_request_saver(
    save_followed_links=_save_followed_article_links,
    entity_from_payload=lambda payload: payload["industry"],
)


_accumulate_industry_saved_counts = make_bucketed_count_accumulator(
    bucket_from_request=lambda request: str(request["payload"]["source_type"]),
    initial_buckets={"listing": 0, "search": 0},
)


def _process_industry_jobs(jobs: list[dict]) -> dict:
    search_jobs = [job for job in jobs if job["source_type"] in {"listing", "search"}]
    direct_article_jobs = [job for job in jobs if job["source_type"] == "article"]
    return run_mixed_job_orchestration(
        search_jobs=search_jobs,
        direct_article_jobs=direct_article_jobs,
        group_jobs_by_url=group_jobs_by_url,
        unique_job_urls=unique_job_urls,
        build_search_save_requests=_build_industry_search_save_requests,
        build_direct_article_save_requests=_build_industry_direct_article_save_requests,
        save_request=_save_industry_request,
        accumulate_saved_count=_accumulate_industry_saved_counts,
    )


def _find_industry(industry_identifier: str) -> dict | None:
    needle = " ".join(industry_identifier.split()).strip().lower()
    if not needle:
        return None

    industries = get_all_industries()

    for industry in industries:
        if str(industry.get("industry_key") or "").strip().lower() == needle:
            return industry

    for industry in industries:
        if str(industry.get("name") or "").strip().lower() == needle:
            return industry

    return None


def list_supported_industries() -> list[dict[str, str]]:
    industries = get_all_industries()
    return [
        {
            "industry_key": str(industry.get("industry_key") or ""),
            "name": str(industry.get("name") or ""),
        }
        for industry in industries
    ]


def get_industry_news(industry_identifier: str) -> int:
    initialize_news_database()
    industry = _find_industry(industry_identifier)
    if industry is None:
        raise ValueError(f"Industry not found for identifier: {industry_identifier}")

    LOGGER.info(
        "Starting single-industry scrape for %s (%s)",
        industry["name"],
        industry["industry_key"],
    )
    jobs = build_industry_source_jobs([industry])
    saved_counts = _process_industry_jobs(jobs)

    industry_counts = saved_counts.get(industry["id"], {"listing": 0, "search": 0})
    listing_saved = industry_counts["listing"]
    search_saved = industry_counts["search"]
    total_saved = listing_saved + search_saved
    LOGGER.info(
        "Finished single-industry scrape for %s (%s): saved %s articles. Log file: %s",
        industry["name"],
        industry["industry_key"],
        total_saved,
        get_log_file_path(),
    )
    print(f"Saved {listing_saved} listing-page articles for {industry['name']}")
    print(f"Saved {search_saved} search-page articles for {industry['name']}")
    print(f"Scrape log written to {get_log_file_path()}")
    return total_saved


def get_all_industry_news() -> None:
    initialize_news_database()
    industries = get_all_industries()
    # Crawl all industry source pages in a single batch, then process listing
    # pages first and search pages second from the normalized crawl results.
    LOGGER.info("Starting all-industry scrape for %s industries", len(industries))
    jobs = build_industry_source_jobs(industries)
    saved_counts = _process_industry_jobs(jobs)

    for industry in industries:
        industry_counts = saved_counts.get(industry["id"], {"listing": 0, "search": 0})
        listing_saved = industry_counts["listing"]
        print(f"Saved {listing_saved} listing-page articles for {industry['name']}")

        search_saved = industry_counts["search"]
        print(f"Saved {search_saved} search-page articles for {industry['name']}")
    LOGGER.info("Finished all-industry scrape. Log file: %s", get_log_file_path())
    print(f"Scrape log written to {get_log_file_path()}")


if __name__ == "__main__":
    #get_all_industry_news()
    get_industry_news("semiconductors")
