"""One bounded HTTP client for non-browser scraping requests."""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from domain.models import FetchResult


DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; StockTradingExperiment/1.0)"


class HttpFetchError(RuntimeError):
    """Raised after a request has exhausted its retry budget."""


class HttpClient:
    """Requests wrapper with a timeout, retry budget, and stable result type."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20,
        max_retries: int = 2,
        backoff_seconds: float = 0.75,
        headers: dict[str, str] | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.session = session or requests.Session()
        self.headers = {"User-Agent": DEFAULT_USER_AGENT, **(headers or {})}

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> FetchResult:
        request_headers = {**self.headers, **(headers or {})}
        last_error = ""

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(
                    url,
                    headers=request_headers,
                    timeout=self.timeout_seconds,
                    allow_redirects=True,
                )
                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    last_error = f"HTTP {response.status_code}"
                else:
                    return FetchResult(
                        request_url=url,
                        url=response.url,
                        status_code=response.status_code,
                        text=response.text,
                        headers=dict(response.headers),
                        error="" if response.status_code < 400 else f"HTTP {response.status_code}",
                    )
            except requests.RequestException as exc:
                last_error = str(exc)

            if attempt < self.max_retries:
                time.sleep(self.backoff_seconds * (2**attempt))

        return FetchResult(
            request_url=url,
            url=url,
            status_code=None,
            error=last_error or "Request failed without an error message.",
        )

    def get_text(self, url: str, *, headers: dict[str, str] | None = None) -> str:
        result = self.get(url, headers=headers)
        if not result.success:
            raise HttpFetchError(f"Unable to fetch {url}: {result.error}")
        return result.text

    def get_json(self, url: str, *, headers: dict[str, str] | None = None) -> Any:
        result = self.get(url, headers=headers)
        if not result.success:
            raise HttpFetchError(f"Unable to fetch {url}: {result.error}")
        try:
            return json.loads(result.text)
        except ValueError as exc:
            raise HttpFetchError(f"Invalid JSON response from {url}: {exc}") from exc
