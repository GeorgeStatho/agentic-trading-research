"""Small, dependency-free contracts for the scraping pipeline.

Keeping these models free of Scrapy, Playwright, and database imports makes the
policy and workflow code straightforward to unit test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class CandidateArticle:
    """An article URL discovered from a feed, search page, or provider API."""

    url: str
    source: str
    source_page_url: str | None = None
    title_hint: str = ""
    published_hint: datetime | None = None


@dataclass(frozen=True, slots=True)
class FetchResult:
    """The normalized outcome of a network fetch."""

    request_url: str
    url: str
    status_code: int | None
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    error: str = ""

    @property
    def success(self) -> bool:
        return not self.error and self.status_code is not None and self.status_code < 400


@dataclass(frozen=True, slots=True)
class ExtractedArticle:
    """Content extracted from an article page before validation and storage."""

    url: str
    source: str
    title: str = ""
    body: str = ""
    published_at: datetime | None = None
    status_code: int | None = None


@dataclass(slots=True)
class ScrapeSummary:
    """Counters and failures for one workflow execution."""

    discovered: int = 0
    skipped_existing: int = 0
    skipped_stale: int = 0
    fetched: int = 0
    saved: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)

    def record_failure(self, url: str, stage: str, error: str) -> None:
        self.failures.append({"url": url, "stage": stage, "error": error})
