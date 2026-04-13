from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from apihelper import (
    ArticleContent,
    ArticleResponse,
    _extract_article_date,
    _extract_text_blocks,
    _normalize_article_text,
    _validate_url_host,
)

BASE_URL = "https://www.gurufocus.com"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 20
DEFAULT_LIMIT = 25

session = requests.Session()
session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }
)


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------

class SourceMode(str, Enum):
    article_only = "article_only"
    article_plus_tabs = "article_plus_tabs"


class LinkItem(BaseModel):
    url: str
    path: str
    title: str
    source: Optional[str] = None
    published_at: Optional[str] = None


class AnalystOpinion(BaseModel):
    gf_value: Optional[str] = None
    warning_signs: Optional[str] = None
    source: Optional[str] = None


class StockData(BaseModel):
    symbol: str
    final_url: str
    articles_url: str
    transcripts_url: str
    articles: list[LinkItem] = Field(default_factory=list)
    transcripts: list[LinkItem] = Field(default_factory=list)
    analyst_opinion: AnalystOpinion = Field(default_factory=AnalystOpinion)


class StockResponse(BaseModel):
    data: StockData
    status: str = "success"


# -----------------------------------------------------------------------------
# Utility helpers
# -----------------------------------------------------------------------------

def _request(url: str, params: Optional[dict] = None) -> requests.Response:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://www.gurufocus.com/",
        "Origin": "https://www.gurufocus.com",
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1",
    }

    try:
        response = session.get(
            url,
            params=params,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=True,
        )

        # Retry once with a fresh session if GuruFocus rejects the first request
        if response.status_code == 403:
            retry_session = requests.Session()
            retry_session.headers.update(headers)
            response = retry_session.get(
                url,
                params=params,
                timeout=DEFAULT_TIMEOUT,
                allow_redirects=True,
            )

        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Request failed for {url}: {exc}") from exc


def _clean_text_local(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text.replace("’", "'").replace("“", '"').replace("”", '"')


def _normalize_path(url: str) -> str:
    match = re.match(r"^https?://[^/]+(?P<path>/.*)$", url)
    return match.group("path") if match else url


def _parse_since(since: Optional[str | date]) -> Optional[date]:
    if since is None:
        return None
    if isinstance(since, date):
        return since
    try:
        return datetime.strptime(since, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="since must be in YYYY-MM-DD format") from exc


def _extract_date_from_path(path: str) -> Optional[date]:
    match = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", path)
    if not match:
        return None
    try:
        year, month, day = map(int, match.groups())
        return date(year, month, day)
    except ValueError:
        return None


def _extract_date_from_text(text: str) -> Optional[date]:
    if not text:
        return None

    cleaned = _clean_text_local(text)

    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass

    match = re.search(r"([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})", cleaned)
    if match:
        for fmt in ("%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(match.group(1), fmt).date()
            except ValueError:
                pass

    return None


def _dedupe_items(items: list[LinkItem]) -> list[LinkItem]:
    seen: set[str] = set()
    out: list[LinkItem] = []
    for item in items:
        if item.url in seen:
            continue
        seen.add(item.url)
        out.append(item)
    return out


def _filter_items_since(items: list[LinkItem], since: Optional[date]) -> list[LinkItem]:
    if since is None:
        return items

    out: list[LinkItem] = []
    for item in items:
        item_date = None
        if item.published_at:
            item_date = _extract_date_from_text(item.published_at)
        if item_date is None:
            item_date = _extract_date_from_path(item.path)

        if item_date is not None and item_date >= since:
            out.append(item)

    return out


def _sort_items_newest_first(items: list[LinkItem]) -> list[LinkItem]:
    def sort_key(item: LinkItem) -> date:
        if item.published_at:
            parsed = _extract_date_from_text(item.published_at)
            if parsed is not None:
                return parsed
        return _extract_date_from_path(item.path) or date.min

    return sorted(items, key=sort_key, reverse=True)


def _apply_limit(items: list[LinkItem], limit: int) -> list[LinkItem]:
    if limit <= 0:
        return []
    return items[:limit]


# -----------------------------------------------------------------------------
# Premium filtering
# -----------------------------------------------------------------------------

PREMIUM_URL_PATTERNS = (
    "/forecast",
    "/guru-trades",
    "/insider",
)

PREMIUM_TEXT_PATTERNS = (
    "subscribe",
    "free trial",
    "premium icon",
    "subscribe to unlock",
    "start 7-day free trial",
    "summarize with guruai",
    "ask guruai",
)


def _looks_premium_url(url: str, path: str, title: str) -> bool:
    haystack = f"{url} {path} {title}".lower()
    return any(token in haystack for token in PREMIUM_URL_PATTERNS + PREMIUM_TEXT_PATTERNS)


def _page_looks_premium(html: str) -> bool:
    text = html.lower()
    return any(token in text for token in PREMIUM_TEXT_PATTERNS)


def _is_accessible_nonpremium_article(url: str) -> bool:
    if _looks_premium_url(url, _normalize_path(url), ""):
        return False

    try:
        response = session.get(url, timeout=12, allow_redirects=True)
        if response.status_code != 200:
            return False
        return not _page_looks_premium(response.text)
    except requests.RequestException:
        return False


# -----------------------------------------------------------------------------
# GuruFocus page resolution
# -----------------------------------------------------------------------------

def _resolve_articles_url(symbol: str) -> str:
    candidate = f"{BASE_URL}/stock/{symbol.upper()}/article"
    response = _request(candidate)
    return response.url


def _derive_page_urls(symbol: str) -> tuple[str, str]:
    symbol = symbol.upper()
    return (
        f"{BASE_URL}/stock/{symbol}/article",
        f"{BASE_URL}/stock/{symbol}/transcripts",
    )


# -----------------------------------------------------------------------------
# Parsing helpers
# -----------------------------------------------------------------------------

def _extract_analyst_opinion(html: str) -> AnalystOpinion:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)

    gf_value = None
    warning_signs = None

    gf_match = re.search(r"GF Value[^$]{0,40}\$?([0-9][0-9,]*(?:\.\d+)?)", text, flags=re.I)
    if gf_match:
        gf_value = gf_match.group(1)

    warn_match = re.search(r"detected\s+([0-9]+)\s+(?:medium|high|low)?\s*warning signs", text, flags=re.I)
    if warn_match:
        warning_signs = warn_match.group(1)

    return AnalystOpinion(
        gf_value=gf_value,
        warning_signs=warning_signs,
        source="GuruFocus",
    )


def _extract_container_metadata(container) -> tuple[Optional[str], Optional[str]]:
    text = _clean_text_local(container.get_text(" ", strip=True))
    source = None
    published_at = None

    source_match = re.search(r"\b(GuruFocus News|GuruFocus)\b", text, re.I)
    if source_match:
        source = _clean_text_local(source_match.group(1))

    date_match = re.search(
        r"([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2})",
        text,
        re.I,
    )
    if date_match:
        published_at = _clean_text_local(date_match.group(1))

    return source, published_at


def _looks_article_path(path: str) -> bool:
    lower = path.lower()

    # keep real news article pages
    if lower.startswith("/news/"):
        return True

    # exclude the stock listing page itself: /stock/AAPL/article
    if re.fullmatch(r"/stock/[a-z0-9.\-]+/article/?", lower):
        return False

    # allow deeper stock/article subpages if GuruFocus ever uses them
    if lower.startswith("/stock/") and "/article/" in lower:
        return True

    return False


def _looks_transcript_path(path: str) -> bool:
    return "/transcripts" in path.lower()


def _looks_stock_relevant(title: str, path: str, symbol: str) -> bool:
    haystack = f"{title} {path}".lower()
    symbol_lower = symbol.lower()
    return symbol_lower in haystack or f"/stock/{symbol_lower}/" in haystack


def _collect_links_from_html(
    html: str,
    symbol: str,
    kind: str,
    skip_subscription_urls: bool = True,
    verify_access: bool = False,
) -> list[LinkItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[LinkItem] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        title = _clean_text_local(anchor.get_text(" ", strip=True))

        if not href or not title:
            continue

        absolute_url = urljoin(BASE_URL, href)
        path = _normalize_path(absolute_url)

        if kind == "articles":
            if not _looks_article_path(path):
                continue
        elif kind == "transcripts":
            if not _looks_transcript_path(path):
                continue
        else:
            continue

        if skip_subscription_urls and _looks_premium_url(absolute_url, path, title):
            continue

        if kind == "articles" and not _looks_stock_relevant(title, path, symbol):
            continue

        parent = anchor.parent if anchor.parent else soup
        source, published_at = _extract_container_metadata(parent)

        items.append(
            LinkItem(
                url=absolute_url,
                path=path,
                title=title,
                source=source,
                published_at=published_at,
            )
        )

    items = _dedupe_items(items)

    if skip_subscription_urls and verify_access:
        items = [item for item in items if _is_accessible_nonpremium_article(item.url)]

    return items

def _page_is_actually_gated(soup: BeautifulSoup) -> bool:
    text = soup.get_text(" ", strip=True).lower()

    strong_gating_signals = [
        "subscribe to unlock",
        "start 7-day free trial",
        "this content is for premium members",
        "premium members only",
    ]

    # If the page has a real headline and enough paragraph content,
    # do not treat sidebar promos as a gated article.
    h1 = soup.find("h1")
    paragraph_count = len(soup.find_all("p"))

    if h1 and paragraph_count >= 5:
        return False

    return any(signal in text for signal in strong_gating_signals)
# -----------------------------------------------------------------------------
# Core function
# -----------------------------------------------------------------------------

def get_stock_data(
    symbol: str,
    since: Optional[str | date] = None,
    limit: int = DEFAULT_LIMIT,
    source_mode: SourceMode = SourceMode.article_plus_tabs,
    skip_subscription_urls: bool = True,
    verify_access: bool = False,
) -> list[dict]:
    symbol = symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    if limit < 0:
        raise HTTPException(status_code=400, detail="limit must be >= 0")

    since_date = _parse_since(since)

    final_url = _resolve_articles_url(symbol)
    articles_url, transcripts_url = _derive_page_urls(symbol)

    article_html = _request(final_url).text

    articles: list[LinkItem] = []
    transcripts: list[LinkItem] = []

    analyst_opinion = _extract_analyst_opinion(article_html)

    articles.extend(
        _collect_links_from_html(
            html=article_html,
            symbol=symbol,
            kind="articles",
            skip_subscription_urls=skip_subscription_urls,
            verify_access=False,
        )
    )

    if source_mode == SourceMode.article_plus_tabs:
        try:
            articles_tab_html = _request(articles_url).text
            articles.extend(
                _collect_links_from_html(
                    html=articles_tab_html,
                    symbol=symbol,
                    kind="articles",
                    skip_subscription_urls=skip_subscription_urls,
                    verify_access=verify_access,
                )
            )
        except HTTPException:
            pass

        try:
            transcripts_html = _request(transcripts_url).text
            transcripts.extend(
                _collect_links_from_html(
                    html=transcripts_html,
                    symbol=symbol,
                    kind="transcripts",
                    skip_subscription_urls=skip_subscription_urls,
                    verify_access=verify_access,
                )
            )
        except HTTPException:
            pass

    articles = _dedupe_items(articles)
    transcripts = _dedupe_items(transcripts)

    if skip_subscription_urls and verify_access:
        articles = [item for item in articles if _is_accessible_nonpremium_article(item.url)]
        transcripts = [item for item in transcripts if _is_accessible_nonpremium_article(item.url)]

    articles = _filter_items_since(articles, since_date)
    transcripts = _filter_items_since(transcripts, since_date)

    articles = _apply_limit(_sort_items_newest_first(articles), limit)
    transcripts = _apply_limit(_sort_items_newest_first(transcripts), limit)

    payload = StockResponse(
        data=StockData(
            symbol=symbol,
            final_url=final_url,
            articles_url=articles_url,
            transcripts_url=transcripts_url,
            articles=articles,
            transcripts=transcripts,
            analyst_opinion=analyst_opinion,
        ),
        status="success",
    )

    return [payload.model_dump()]


def get_article_content(article_url: str, reject_premium: bool = True) -> dict:
    _validate_url_host(article_url, ("gurufocus.com", "www.gurufocus.com"))

    response = _request(article_url)
    html = response.text
    soup = BeautifulSoup(html, "html.parser")

    if reject_premium and _page_is_actually_gated(soup):
        raise HTTPException(
            status_code=403,
            detail="This GuruFocus page appears to require subscription access."
        )

    title = None
    h1 = soup.find("h1")
    if h1:
        title = _clean_text_local(h1.get_text(" ", strip=True))

    article_container = None
    candidates = [
        soup.find("article"),
        soup.find("main"),
        soup.find("div", class_=re.compile("article|content|news", re.I)),
    ]
    for candidate in candidates:
        if candidate and len(candidate.get_text(" ", strip=True)) > 300:
            article_container = candidate
            break

    if not article_container:
        article_container = soup

    blocks = _extract_text_blocks(article_container)

    filtered_blocks = []
    noise_patterns = [
        r"^Key Takeaways:$",
        r"^Summary$",
        r"^Image: ",
        r"^GuruFocus News$",
        r"^View more$",
    ]

    for block in blocks:
        if any(re.search(pattern, block, flags=re.I) for pattern in noise_patterns):
            continue
        filtered_blocks.append(block)

    body = _normalize_article_text("\n\n".join(filtered_blocks))
    published_at = _extract_article_date(soup)

    if not title:
        raise HTTPException(status_code=422, detail="Could not extract article title from GuruFocus page.")
    if not body:
        raise HTTPException(status_code=422, detail="Could not extract article body from GuruFocus page.")

    payload = ArticleResponse(
        data=ArticleContent(
            url=article_url,
            title=title,
            published_at=published_at,
            body=body,
        )
    )
    return payload.model_dump()


# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------

app = FastAPI(
    title="GuruFocus Stock URL API",
    version="1.1.0",
    description=(
        "Returns GuruFocus stock article links, transcript links, and best-effort "
        "metadata using the /article stock page as the primary source."
    ),
)


@app.get("/stocks/{symbol}", response_model=list[StockResponse])
def get_stock(
    symbol: str,
    since: Optional[str] = Query(None, description="Only include items on or after YYYY-MM-DD"),
    limit: int = Query(DEFAULT_LIMIT, ge=0, le=250),
    source_mode: SourceMode = Query(SourceMode.article_plus_tabs),
    skip_subscription_urls: bool = Query(True, description="Skip premium/subscription-style URLs"),
    verify_access: bool = Query(
        False,
        description="Fetch candidate pages and exclude pages that still look gated",
    ),
):
    return get_stock_data(
        symbol=symbol,
        since=since,
        limit=limit,
        source_mode=source_mode,
        skip_subscription_urls=skip_subscription_urls,
        verify_access=verify_access,
    )


@app.get("/stock", response_model=list[StockResponse])
def get_stock_query(
    symbol: str = Query(..., min_length=1),
    since: Optional[str] = Query(None, description="Only include items on or after YYYY-MM-DD"),
    limit: int = Query(DEFAULT_LIMIT, ge=0, le=250),
    source_mode: SourceMode = Query(SourceMode.article_plus_tabs),
    skip_subscription_urls: bool = Query(True),
    verify_access: bool = Query(False),
):
    return get_stock_data(
        symbol=symbol,
        since=since,
        limit=limit,
        source_mode=source_mode,
        skip_subscription_urls=skip_subscription_urls,
        verify_access=verify_access,
    )


@app.get("/article", response_model=ArticleResponse)
def get_article(
    url: str = Query(..., description="Full GuruFocus article URL"),
    reject_premium: bool = Query(True, description="Reject premium/gated article pages"),
):
    return get_article_content(url, reject_premium=reject_premium)


@app.get("/articles/content", response_model=ArticleResponse)
def get_article_content_route(
    url: str = Query(..., description="Full GuruFocus article URL"),
    reject_premium: bool = Query(True),
):
    return get_article_content(url, reject_premium=reject_premium)


if __name__ == "__main__":
    import json

    test_symbol = "AAPL"
    print(f"Testing get_stock_data('{test_symbol}')")
    stock_result = get_stock_data(test_symbol, limit=5)
    print(json.dumps(stock_result, indent=2))

    data = stock_result[0].get("data", {}) if stock_result else {}
    article_items = data.get("articles") or []

    real_articles = [
        item for item in article_items
        if "/news/" in item.get("path", "")
    ]

    if real_articles:
        test_url = real_articles[0]["url"]
        print(f"\nTesting get_article_content('{test_url}')")
        article_result = get_article_content(test_url)
        print(json.dumps(article_result, indent=2))
    else:
        print("\nNo real article URLs found to test.")
