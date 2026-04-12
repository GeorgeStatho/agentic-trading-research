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
BASE_URL = "https://www.investing.com"

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
    quote_only = "quote_only"
    quote_plus_tabs = "quote_plus_tabs"


class LinkItem(BaseModel):
    url: str
    path: str
    title: str
    source: Optional[str] = None
    published_at: Optional[str] = None


class AnalystOpinion(BaseModel):
    rating: Optional[str] = None
    price_target: Optional[str] = None
    source: Optional[str] = None


class StockData(BaseModel):
    symbol: str
    company_slug: str
    final_url: str
    news_url: Optional[str] = None
    opinion_url: Optional[str] = None
    earnings_url: Optional[str] = None
    articles: list[LinkItem] = Field(default_factory=list)
    analysis: list[LinkItem] = Field(default_factory=list)
    transcripts: list[LinkItem] = Field(default_factory=list)
    analyst_opinion: AnalystOpinion = Field(default_factory=AnalystOpinion)


class StockResponse(BaseModel):
    data: StockData
    status: str = "success"


# -----------------------------------------------------------------------------
# Utility helpers
# -----------------------------------------------------------------------------

def _request(url: str, params: Optional[dict] = None) -> requests.Response:
    try:
        response = session.get(
            url,
            params=params,
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=True,
        )
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Request failed for {url}: {exc}") from exc


def _clean_text(text: str) -> str:
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

    cleaned = _clean_text(text)
    cleaned = re.sub(r"^[•\-\s]+", "", cleaned)

    patterns = [
        "%b %d, %Y",
        "%B %d, %Y",
        "%b %d %Y",
        "%B %d %Y",
    ]

    for fmt in patterns:
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
# Subscription / premium filtering
# -----------------------------------------------------------------------------

PREMIUM_URL_PATTERNS = (
    "/pro/",
    "/academy/investing-pro/",
)

PREMIUM_TEXT_PATTERNS = (
    "investingpro",
    "premium",
    "subscribe",
    "subscription",
    "sign in to continue",
    "unlock this article",
    "members only",
)


def _looks_premium_url(url: str, path: str, title: str) -> bool:
    haystack = f"{url} {path} {title}".lower()
    return any(pattern in haystack for pattern in PREMIUM_URL_PATTERNS + PREMIUM_TEXT_PATTERNS)


def _page_looks_premium(html: str) -> bool:
    text = html.lower()
    return any(pattern in text for pattern in PREMIUM_TEXT_PATTERNS)


def _is_accessible_nonpremium_article(url: str) -> bool:
    """
    Best-effort validation.
    """
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
# Investing.com page resolution
# -----------------------------------------------------------------------------

def _resolve_equity_slug(symbol: str) -> str:
    symbol = symbol.upper()

    fallback_map = {
        "AAPL": "apple-computer-inc",
        "MSFT": "microsoft-corp",
        "GOOGL": "google-inc",
        "GOOG": "alphabet-inc-c",
        "AMZN": "amazon-com-inc",
        "META": "facebook-inc",
        "TSLA": "tesla-motors",
        "NVDA": "nvidia-corp",
    }

    # Prefer known good slugs first for major names
    if symbol in fallback_map:
        return fallback_map[symbol]

    disallowed_slugs = {
        "trending-stocks",
        "most-active-stocks",
        "top-stocks",
        "world-indices",
        "commodities",
        "currencies",
        "crypto",
    }

    search_candidates = [
        f"{BASE_URL}/search/?q={symbol}",
        f"{BASE_URL}/search/?q={symbol}%20stock",
    ]

    found_slugs: list[str] = []

    for search_url in search_candidates:
        try:
            response = session.get(search_url, timeout=15, allow_redirects=True)
            if response.status_code != 200:
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            for anchor in soup.find_all("a", href=True):
                href = anchor["href"].strip()
                path = _normalize_path(urljoin(BASE_URL, href))
                match = re.match(r"^/equities/([^/?#]+)$", path)
                if not match:
                    continue

                slug = match.group(1).lower()
                if slug in disallowed_slugs:
                    continue

                found_slugs.append(slug)

        except requests.RequestException:
            continue

    if found_slugs:
        # Prefer slugs containing a known company token from fallback map when available
        preferred = fallback_map.get(symbol, "").lower()
        if preferred and preferred in found_slugs:
            return preferred

        # Otherwise return the first non-generic slug
        return found_slugs[0]

    raise HTTPException(
        status_code=404,
        detail=f"Could not resolve Investing.com equity slug for symbol '{symbol}'.",
    )


def _resolve_quote_url(symbol: str, company_slug: str) -> str:
    candidate = f"{BASE_URL}/equities/{company_slug}"
    response = _request(candidate)
    text = response.text.lower()

    if symbol.lower() not in text and company_slug.split("-")[0] not in text:
        # Still return the resolved page if it exists; Investing pages can vary in markup.
        return response.url

    return response.url


def _derive_page_urls(company_slug: str) -> tuple[str, str, str]:
    base = f"{BASE_URL}/equities/{company_slug}"
    return (
        f"{base}-news",
        f"{base}-opinion",
        f"{base}-earnings",
    )


# -----------------------------------------------------------------------------
# Parsers
# -----------------------------------------------------------------------------

def _extract_title_source_date_from_container(container: BeautifulSoup) -> tuple[Optional[str], Optional[str], Optional[str]]:
    title = None
    source = None
    published_at = None

    text = _clean_text(container.get_text(" ", strip=True))
    if text:
        title = text

    # Very loose extraction from snippets like:
    # ByInvesting.com •11 hours ago
    # ByReuters •Oct 21, 2025
    source_match = re.search(r"\bBy\s*([A-Za-z0-9 .,&\-]+)", text)
    if source_match:
        source = _clean_text(source_match.group(1))

    date_match = re.search(
        r"(?:•\s*)?([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4}|(?:\d+\s+hours?\s+ago|\d+\s+days?\s+ago|today|yesterday))",
        text,
        flags=re.IGNORECASE,
    )
    if date_match:
        published_at = _clean_text(date_match.group(1))

    return title, source, published_at


def _collect_links_from_page(
    html: str,
    kind: str,
    skip_premium: bool = True,
    verify_access: bool = False,
) -> list[LinkItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[LinkItem] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href:
            continue

        absolute_url = urljoin(BASE_URL, href)
        path = _normalize_path(absolute_url)
        title = _clean_text(anchor.get_text(" ", strip=True))

        if not title:
            continue

        is_match = False

        if kind == "news":
            is_match = (
                "/news/" in path.lower()
                or path.lower().endswith("-news")
                or path.lower().startswith("/analysis/")
            )
        elif kind == "analysis":
            is_match = "/analysis/" in path.lower() or path.lower().endswith("-opinion")
        elif kind == "transcripts":
            is_match = "/news/transcripts/" in path.lower()

        if not is_match:
            continue

        if skip_premium and _looks_premium_url(absolute_url, path, title):
            continue

        container = anchor.parent if anchor.parent else soup
        _, source, published_at = _extract_title_source_date_from_container(container)

        item = LinkItem(
            url=absolute_url,
            path=path,
            title=title,
            source=source,
            published_at=published_at,
        )

        items.append(item)

    items = _dedupe_items(items)

    if skip_premium and verify_access:
        items = [item for item in items if _is_accessible_nonpremium_article(item.url)]

    return items


def _collect_transcripts_for_symbol(symbol: str, company_slug: str, limit: int = 20) -> list[LinkItem]:
    """
    Best-effort transcript search using Investing search pages.
    """
    queries = [
        f"{symbol} transcript",
        f"{symbol} earnings call transcript",
        company_slug.replace("-", " ") + " transcript",
    ]

    items: list[LinkItem] = []

    for query in queries:
        search_url = f"{BASE_URL}/search/?q={requests.utils.quote(query)}"
        try:
            response = session.get(search_url, timeout=15, allow_redirects=True)
            if response.status_code != 200:
                continue

            page_items = _collect_links_from_page(
                html=response.text,
                kind="transcripts",
                skip_premium=True,
                verify_access=False,
            )

            # Keep only likely relevant transcript links.
            for item in page_items:
                haystack = f"{item.title} {item.path}".lower()
                if symbol.lower() in haystack or company_slug.split("-")[0] in haystack:
                    items.append(item)

        except requests.RequestException:
            continue

    items = _dedupe_items(items)
    items = _sort_items_newest_first(items)
    return items[:limit]


def _extract_analyst_opinion_from_earnings(html: str) -> AnalystOpinion:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)

    rating = None
    price_target = None
    source = None

    rating_match = re.search(
        r"\b(Outperform|Overweight|Buy|Hold|Sell|Underperform|Neutral)\b",
        text,
        flags=re.IGNORECASE,
    )
    if rating_match:
        rating = rating_match.group(1).title()

    price_target_match = re.search(
        r"\$([0-9]+(?:\.[0-9]+)?)\s+price target",
        text,
        flags=re.IGNORECASE,
    )
    if price_target_match:
        price_target = price_target_match.group(1)

    source_match = re.search(r"\b(By[A-Za-z0-9 .,&\-]+)", text)
    if source_match:
        source = _clean_text(source_match.group(1).replace("By", "", 1))

    return AnalystOpinion(
        rating=rating,
        price_target=price_target,
        source=source,
    )


# -----------------------------------------------------------------------------
# Core function
# -----------------------------------------------------------------------------

def get_stock_data(
    symbol: str,
    since: Optional[str | date] = None,
    limit: int = DEFAULT_LIMIT,
    source_mode: SourceMode = SourceMode.quote_plus_tabs,
    skip_subscription_urls: bool = True,
    verify_access: bool = False,
) -> list[dict]:
    symbol = symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    if limit < 0:
        raise HTTPException(status_code=400, detail="limit must be >= 0")

    since_date = _parse_since(since)

    company_slug = _resolve_equity_slug(symbol)
    final_url = _resolve_quote_url(symbol, company_slug)
    news_url, opinion_url, earnings_url = _derive_page_urls(company_slug)

    quote_html = _request(final_url).text

    articles: list[LinkItem] = []
    analysis: list[LinkItem] = []
    transcripts: list[LinkItem] = []

    if source_mode in {SourceMode.quote_only, SourceMode.quote_plus_tabs}:
        articles.extend(
            _collect_links_from_page(
                html=quote_html,
                kind="news",
                skip_premium=skip_subscription_urls,
                verify_access=False,
            )
        )
        analysis.extend(
            _collect_links_from_page(
                html=quote_html,
                kind="analysis",
                skip_premium=skip_subscription_urls,
                verify_access=False,
            )
        )

    analyst_opinion = AnalystOpinion()

    if source_mode == SourceMode.quote_plus_tabs:
        try:
            news_html = _request(news_url).text
            articles.extend(
                _collect_links_from_page(
                    html=news_html,
                    kind="news",
                    skip_premium=skip_subscription_urls,
                    verify_access=verify_access,
                )
            )
        except HTTPException:
            pass

        try:
            opinion_html = _request(opinion_url).text
            analysis.extend(
                _collect_links_from_page(
                    html=opinion_html,
                    kind="analysis",
                    skip_premium=skip_subscription_urls,
                    verify_access=verify_access,
                )
            )
        except HTTPException:
            pass

        try:
            earnings_html = _request(earnings_url).text
            analyst_opinion = _extract_analyst_opinion_from_earnings(earnings_html)
        except HTTPException:
            pass

        transcripts.extend(_collect_transcripts_for_symbol(symbol, company_slug, limit=limit * 2))

    articles = _dedupe_items(articles)
    analysis = _dedupe_items(analysis)
    transcripts = _dedupe_items(transcripts)

    if skip_subscription_urls and verify_access:
        articles = [item for item in articles if _is_accessible_nonpremium_article(item.url)]
        analysis = [item for item in analysis if _is_accessible_nonpremium_article(item.url)]
        transcripts = [item for item in transcripts if _is_accessible_nonpremium_article(item.url)]

    articles = _filter_items_since(articles, since_date)
    analysis = _filter_items_since(analysis, since_date)
    transcripts = _filter_items_since(transcripts, since_date)

    articles = _apply_limit(_sort_items_newest_first(articles), limit)
    analysis = _apply_limit(_sort_items_newest_first(analysis), limit)
    transcripts = _apply_limit(_sort_items_newest_first(transcripts), limit)

    payload = StockResponse(
        data=StockData(
            symbol=symbol,
            company_slug=company_slug,
            final_url=final_url,
            news_url=news_url,
            opinion_url=opinion_url,
            earnings_url=earnings_url,
            articles=articles,
            analysis=analysis,
            transcripts=transcripts,
            analyst_opinion=analyst_opinion,
        ),
        status="success",
    )

    return [payload.model_dump()]

def get_article_content(article_url: str, reject_premium: bool = True) -> dict[str, Any]:
    _validate_url_host(article_url, ("investing.com", "www.investing.com"))

    response = _request(article_url)
    html = response.text

    if reject_premium and _page_looks_premium(html):
        raise HTTPException(status_code=403, detail="This Investing.com article appears to require a subscription or premium access.")

    soup = BeautifulSoup(html, "html.parser")

    title = None
    h1 = soup.find("h1")
    if h1:
        title = _clean_text(h1.get_text(" ", strip=True))

    article_container = None
    candidates = [
        soup.find("article"),
        soup.find("main"),
        soup.find("div", class_=re.compile("article|content", re.I)),
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
        r"^Get premium news and insight by upgrading to InvestingPro",
        r"^Read more$",
        r"^Disclaimer:",
        r"^Related Articles$",
    ]

    for block in blocks:
        if any(re.search(pattern, block, flags=re.I) for pattern in noise_patterns):
            continue
        if reject_premium and _looks_premium_url(article_url, _normalize_path(article_url), block):
            continue
        filtered_blocks.append(block)

    body = _normalize_article_text("\n\n".join(filtered_blocks))
    published_at = _extract_article_date(soup)
    if not title:
        raise HTTPException(status_code=422, detail="Could not extract article title from Fool page.")
    if not body:
        raise HTTPException(status_code=422, detail="Could not extract article body from Fool page.")

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
    title="Investing.com Stock URL API",
    version="1.0.0",
    description=(
        "Returns Investing.com news, analysis, transcript links, and metadata "
        "for a stock symbol while skipping premium/subscription-style URLs."
    ),
)


@app.get("/stocks/{symbol}", response_model=list[StockResponse])
def get_stock(
    symbol: str,
    since: Optional[str] = Query(None, description="Only include items on or after YYYY-MM-DD"),
    limit: int = Query(DEFAULT_LIMIT, ge=0, le=250),
    source_mode: SourceMode = Query(SourceMode.quote_plus_tabs),
    skip_subscription_urls: bool = Query(True, description="Skip premium/subscription-style URLs"),
    verify_access: bool = Query(
        False,
        description="Fetch each article page and exclude pages that still look premium",
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
    source_mode: SourceMode = Query(SourceMode.quote_plus_tabs),
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
    url: str = Query(..., description="Full Investing.com article URL"),
    reject_premium: bool = Query(True, description="Reject premium/gated article pages"),
):
    return get_article_content(url, reject_premium=reject_premium)


@app.get("/articles/content", response_model=ArticleResponse)
def get_article_content_route(
    url: str = Query(..., description="Full Investing.com article URL"),
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
    article_items = (data.get("articles") or []) + (data.get("analysis") or [])
    if article_items:
        test_url = article_items[0]["url"]
        print(f"\nTesting get_article_content('{test_url}')")
        article_result = get_article_content(test_url)
        print(json.dumps(article_result, indent=2))
