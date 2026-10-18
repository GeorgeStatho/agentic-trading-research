from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, NamedTuple
from urllib.parse import urlsplit

from core.CommonPipeline import MAX_ARTICLE_AGE_DAYS, is_recent_article
from news_db_modules.queries import get_saved_and_failed_urls
from pipelines._internal import is_blacklisted_cnbc_link
from processing.news_normalization import normalize_url
from sources.source_config import get_article_patterns, get_max_article_age_days, is_allowed_source

CNBc_URL_DATE_RE = re.compile(r"/(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/")
MAX_FAILURES = 3


def _extract_cnbc_url_published_at(url: str) -> str | None:
    lowered = str(url or "").strip().lower()
    if "newswire.example.com" not in lowered:
        return None

    match = CNBc_URL_DATE_RE.search(urlsplit(lowered).path)
    if match is None:
        return None

    try:
        published_at = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None

    return published_at.isoformat()


@dataclass(frozen=True)
class LinkVerdict:
    link: dict
    href: str
    normalized_url: str
    action: str  # "fetch" | "reuse" | "skip"
    reason: str = ""
    max_age_days: int = MAX_ARTICLE_AGE_DAYS
    article_id: int | None = None


class LinkBatch(NamedTuple):
    links: list[dict]
    max_articles: int  # cap on "fetch" verdicts in this batch; reuse never counts
    may_reuse: Callable[[dict], bool] | None = None  # None = any recent existing article may be reused


def _skip(link: dict, href: str, normalized_url: str, reason: str, max_age_days: int = MAX_ARTICLE_AGE_DAYS) -> LinkVerdict:
    return LinkVerdict(link, href, normalized_url, "skip", reason, max_age_days)


def _prejudge(
    link: dict,
    seen: set[str],
    should_include_link: Callable[[str, dict], bool] | None,
) -> LinkVerdict:
    # Checks that need no database; a surviving link comes back as a tentative "fetch".
    href = str(link.get("href") or "").strip()
    if not href:
        return _skip(link, "", "", "no_href")

    normalized_url = normalize_url(href)
    max_age_days = get_max_article_age_days(href, MAX_ARTICLE_AGE_DAYS)

    def skip(reason: str) -> LinkVerdict:
        return _skip(link, href, normalized_url, reason, max_age_days)

    if normalized_url in seen:
        return skip("duplicate")
    seen.add(normalized_url)
    if should_include_link is not None and not should_include_link(href, link):
        return skip("excluded")
    if not is_allowed_source(href):
        return skip("disallowed_source")
    patterns = get_article_patterns(href)
    if patterns and not any(pattern in href for pattern in patterns):
        return skip("not_article_url")
    if is_blacklisted_cnbc_link(href):
        return skip("blacklisted")
    url_published_at = _extract_cnbc_url_published_at(href)
    if url_published_at and not is_recent_article(url_published_at, max_age_days=max_age_days):
        return skip("stale_url_date")
    return LinkVerdict(link, href, normalized_url, "fetch", max_age_days=max_age_days)


def _judge_with_database(
    verdict: LinkVerdict,
    may_reuse: Callable[[dict], bool] | None,
    saved: dict[str, dict],
    failed: dict[str, dict],
) -> LinkVerdict:
    existing = saved.get(verdict.normalized_url)
    if existing is not None:
        if not is_recent_article(existing["published_at"], max_age_days=verdict.max_age_days):
            return replace(verdict, action="skip", reason="stale_existing")
        if may_reuse is not None and not may_reuse(verdict.link):
            return replace(verdict, action="skip", reason="irrelevant_existing")
        return replace(verdict, action="reuse", article_id=existing["id"])

    failure = failed.get(verdict.normalized_url)
    if failure is not None and (failure["is_permanent"] or failure["failure_count"] >= MAX_FAILURES):
        return replace(verdict, action="skip", reason="failed_url")
    return verdict


def judge_links(
    batches: list[LinkBatch],
    *,
    should_include_link: Callable[[str, dict], bool] | None = None,
) -> list[list[LinkVerdict]]:
    # One verdict per link, in input order. Pure decision: no logging, no DB writes.
    prejudged = []
    for batch in batches:
        seen: set[str] = set()
        prejudged.append([_prejudge(link, seen, should_include_link) for link in batch.links])

    survivors = {v.normalized_url for verdicts in prejudged for v in verdicts if v.action == "fetch"}
    saved, failed = get_saved_and_failed_urls(sorted(survivors)) if survivors else ({}, {})

    results = []
    for batch, verdicts in zip(batches, prejudged):
        fetches = 0
        judged = []
        for verdict in verdicts:
            if verdict.action == "fetch":
                verdict = _judge_with_database(verdict, batch.may_reuse, saved, failed)
            if verdict.action == "fetch":
                if fetches >= batch.max_articles:
                    verdict = replace(verdict, action="skip", reason="limit")
                else:
                    fetches += 1
            judged.append(verdict)
        results.append(judged)
    return results
