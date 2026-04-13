from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup, Tag, NavigableString
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from apihelper import (
    ArticleContent,
    ArticleResponse,
    _extract_article_date,
    _normalize_article_text,
    _validate_url_host,
)

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

BASE_URL = "https://www.fool.com"
PUBLIC_INSTRUMENT_SEARCH_URL = "https://api.fool.com/quotes/v4/instruments/search/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

COMMON_EXCHANGES = [
    "nasdaq",
    "nyse",
    "amex",
    "otc",
    "crypto",
    "tsx",
    "cse",
    "fra",
    "etr",
    "lse",
    "asx",
    "unq",
]

DEFAULT_TIMEOUT = 20
DEFAULT_LIMIT = 25
DEFAULT_SEARCH_PAGES = 3

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
    quote_plus_search = "quote_plus_search"


class LinkItem(BaseModel):
    url: str
    path: str
    title: str


class AnalystOpinions(BaseModel):
    rating: Optional[str] = None
    source: Optional[str] = None


class StockData(BaseModel):
    symbol: str
    articles: list[LinkItem] = Field(default_factory=list)
    final_url: str
    transcripts: list[LinkItem] = Field(default_factory=list)
    instrument_id: Optional[int] = None
    analyst_opinions: AnalystOpinions = Field(default_factory=AnalystOpinions)


class StockResponse(BaseModel):
    data: StockData
    status: str = "success"


# -----------------------------------------------------------------------------
# Utility helpers
# -----------------------------------------------------------------------------

def _request(url: str, params: Optional[dict[str, Any]] = None) -> requests.Response:
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
    return text.replace("\xa0", " ").replace("’", "'").replace("“", '"').replace("”", '"')


def _normalize_path(url: str) -> str:
    match = re.match(r"^https?://[^/]+(?P<path>/.*)$", url)
    return match.group("path") if match else url


def _extract_date_from_path(path: str) -> Optional[date]:
    match = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", path)
    if not match:
        return None

    try:
        year, month, day = map(int, match.groups())
        return date(year, month, day)
    except ValueError:
        return None


def _parse_since(since: Optional[str | date]) -> Optional[date]:
    if since is None:
        return None

    if isinstance(since, date):
        return since

    try:
        return datetime.strptime(since, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="since must be in YYYY-MM-DD format") from exc


def _dedupe_items(items: list[LinkItem]) -> list[LinkItem]:
    seen: set[str] = set()
    deduped: list[LinkItem] = []

    for item in items:
        if item.url in seen:
            continue
        seen.add(item.url)
        deduped.append(item)

    return deduped


def _filter_items_since(items: list[LinkItem], since: Optional[date]) -> list[LinkItem]:
    if since is None:
        return items

    filtered: list[LinkItem] = []
    for item in items:
        item_date = _extract_date_from_path(item.path)
        if item_date is not None and item_date >= since:
            filtered.append(item)

    return filtered


def _sort_items_newest_first(items: list[LinkItem]) -> list[LinkItem]:
    return sorted(
        items,
        key=lambda item: _extract_date_from_path(item.path) or date.min,
        reverse=True,
    )


def _apply_limit(items: list[LinkItem], limit: int) -> list[LinkItem]:
    if limit <= 0:
        return []
    return items[:limit]


def _is_article_path(path: str) -> bool:
    return path.startswith(
        (
            "/investing/",
            "/market/",
            "/retirement/",
            "/money/",
            "/personal-finance/",
        )
    )


def _is_transcript_path(path: str) -> bool:
    return path.startswith("/earnings/call-transcripts/")


def _looks_relevant(text: str, path: str, symbol: str, company_name: Optional[str]) -> bool:
    text_lower = text.lower()
    path_lower = path.lower()
    symbol_lower = symbol.lower()
    company_lower = (company_name or "").lower()

    return (
        symbol_lower in text_lower
        or symbol_lower in path_lower
        or (company_lower and company_lower in text_lower)
        or (company_lower and company_lower in path_lower)
    )


# -----------------------------------------------------------------------------
# Fool-specific stock page helpers
# -----------------------------------------------------------------------------

def _extract_company_name(soup: BeautifulSoup) -> Optional[str]:
    h1 = soup.find("h1")
    if not h1:
        return None
    company_name = _clean_text(h1.get_text(" ", strip=True))
    return company_name or None


def _extract_analyst_opinion(soup: BeautifulSoup) -> AnalystOpinions:
    text = soup.get_text(" ", strip=True)

    patterns = [
        r"(Motley Fool Stock Advisor).{0,80}?\b(Buy|Hold|Sell)\b",
        r"(Stock Advisor).{0,80}?\b(Buy|Hold|Sell)\b",
        r"\b(Buy|Hold|Sell)\b.{0,80}?(Motley Fool Stock Advisor)",
        r"\b(Buy|Hold|Sell)\b.{0,80}?(Stock Advisor)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue

        source: Optional[str] = None
        rating: Optional[str] = None

        for part in match.groups():
            if not part:
                continue
            if part.lower() in {"buy", "hold", "sell"}:
                rating = part.title()
            else:
                source = _clean_text(part)

        return AnalystOpinions(rating=rating, source=source)

    return AnalystOpinions()


def _resolve_instrument(symbol: str) -> dict[str, Any]:
    response = _request(
        PUBLIC_INSTRUMENT_SEARCH_URL,
        params={
            "maxResults": 10,
            "apikey": "public",
            "domain": "fool.com",
            "query": symbol,
        },
    )

    try:
        payload = response.json()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Instrument lookup returned non-JSON response: {exc}",
        ) from exc

    candidates: list[dict[str, Any]] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if any(key.lower() in {"symbol", "ticker"} for key in obj.keys()):
                candidates.append(obj)
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    def read_ci(data: dict[str, Any], *names: str) -> Any:
        for name in names:
            for key, value in data.items():
                if key.lower() == name.lower():
                    return value
        return None

    walk(payload)

    chosen: dict[str, Any] = {}
    for candidate in candidates:
        candidate_symbol = read_ci(candidate, "symbol", "ticker")
        if isinstance(candidate_symbol, str) and candidate_symbol.upper() == symbol.upper():
            chosen = candidate
            break

    if not chosen and candidates:
        chosen = candidates[0]

    instrument_id = read_ci(chosen, "instrumentId", "instrument_id", "id")
    company_name = read_ci(chosen, "name", "companyName", "company_name")
    exchange = read_ci(chosen, "exchange", "exchangeCode", "market", "mic")
    url = read_ci(chosen, "url", "quoteUrl", "quote_url")

    return {
        "instrument_id": instrument_id if isinstance(instrument_id, int) else None,
        "company_name": company_name if isinstance(company_name, str) else None,
        "exchange": exchange.lower() if isinstance(exchange, str) else None,
        "url": url if isinstance(url, str) else None,
    }


def _resolve_quote_url(symbol: str, lookup: dict[str, Any]) -> str:
    existing_url = lookup.get("url")
    if isinstance(existing_url, str) and existing_url.startswith("http"):
        return existing_url

    exchange = lookup.get("exchange")
    exchanges_to_try = [exchange] if exchange else []
    exchanges_to_try.extend([item for item in COMMON_EXCHANGES if item != exchange])

    symbol_lower = symbol.lower()

    for exchange_name in exchanges_to_try:
        candidate_url = f"{BASE_URL}/quote/{exchange_name}/{symbol_lower}/"
        try:
            response = session.get(candidate_url, timeout=15, allow_redirects=True)
            if response.status_code == 200 and "/quote/" in response.url:
                return response.url
        except requests.RequestException:
            continue

    raise HTTPException(
        status_code=404,
        detail=f"Could not resolve Fool quote page for symbol '{symbol}'.",
    )


def _collect_quote_page_links(html: str) -> tuple[list[LinkItem], list[LinkItem]]:
    soup = BeautifulSoup(html, "html.parser")
    articles: list[LinkItem] = []
    transcripts: list[LinkItem] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        title = _clean_text(anchor.get_text(" ", strip=True))

        if not href or not title:
            continue

        absolute_url = urljoin(BASE_URL, href)
        path = _normalize_path(absolute_url)

        if _is_article_path(path):
            articles.append(LinkItem(url=absolute_url, path=path, title=title))
        elif _is_transcript_path(path):
            transcripts.append(LinkItem(url=absolute_url, path=path, title=title))

    return _dedupe_items(articles), _dedupe_items(transcripts)


def _collect_search_page_links(
    html: str,
    symbol: str,
    company_name: Optional[str],
) -> tuple[list[LinkItem], list[LinkItem]]:
    soup = BeautifulSoup(html, "html.parser")
    articles: list[LinkItem] = []
    transcripts: list[LinkItem] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        title = _clean_text(anchor.get_text(" ", strip=True))

        if not href or not title:
            continue

        absolute_url = urljoin(BASE_URL, href)
        path = _normalize_path(absolute_url)

        if _is_article_path(path) and _looks_relevant(title, path, symbol, company_name):
            articles.append(LinkItem(url=absolute_url, path=path, title=title))
        elif _is_transcript_path(path) and _looks_relevant(title, path, symbol, company_name):
            transcripts.append(LinkItem(url=absolute_url, path=path, title=title))

    return _dedupe_items(articles), _dedupe_items(transcripts)


def _search_fool_site_pages(
    symbol: str,
    company_name: Optional[str],
    max_pages: int = DEFAULT_SEARCH_PAGES,
) -> tuple[list[LinkItem], list[LinkItem]]:
    queries: list[str] = [symbol, f"{symbol} stock"]
    if company_name:
        queries.extend([company_name, f"{company_name} stock"])

    all_articles: list[LinkItem] = []
    all_transcripts: list[LinkItem] = []

    seen_search_urls: set[str] = set()

    for query in queries:
        for page in range(1, max_pages + 1):
            candidate_urls = [
                f"{BASE_URL}/search/?q={quote_plus(query)}&page={page}",
                f"{BASE_URL}/search?query={quote_plus(query)}&page={page}",
            ]

            got_results_this_page = False

            for search_url in candidate_urls:
                if search_url in seen_search_urls:
                    continue
                seen_search_urls.add(search_url)

                try:
                    response = session.get(search_url, timeout=15, allow_redirects=True)
                    if response.status_code != 200:
                        continue

                    html = response.text
                    if "<html" not in html.lower():
                        continue

                    articles, transcripts = _collect_search_page_links(
                        html=html,
                        symbol=symbol,
                        company_name=company_name,
                    )

                    if articles or transcripts:
                        got_results_this_page = True
                        all_articles.extend(articles)
                        all_transcripts.extend(transcripts)

                except requests.RequestException:
                    continue

            if not got_results_this_page:
                break

    return _dedupe_items(all_articles), _dedupe_items(all_transcripts)


# -----------------------------------------------------------------------------
# Robust Fool article extractor
# -----------------------------------------------------------------------------

_SKIP_EXACT = {
    "Key Points",
    "Expand",
    "Read Next",
    "Today's Change",
    "Current Price",
    "Key Data Points",
    "Market Cap",
    "Day's Range",
    "52wk Range",
    "Volume",
    "Avg Vol",
    "Gross Margin",
    "Dividend Yield",
}

_SKIP_PREFIXES = (
    "Image source:",
    "By ",
    "Updated ",
    "Published ",
    "NASDAQ : ",
    "NYSE : ",
)

_STOP_MARKERS = {
    "Read Next",
}

def _is_noise_block(text: str) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return True
    if cleaned in _SKIP_EXACT:
        return True
    if any(cleaned.startswith(prefix) for prefix in _SKIP_PREFIXES):
        return True
    if re.match(r"^[A-Z]{1,8}\s*:\s*[A-Z]{1,8}$", cleaned):
        return True
    if re.match(r"^[\(\-+]?\d", cleaned) and len(cleaned) < 20:
        return True
    return False


def _select_fool_article_container(soup: BeautifulSoup) -> Tag:
    candidates = [
        soup.find("article"),
        soup.find("main"),
        soup.find(attrs={"data-test": re.compile(r"article|content|body", re.I)}),
        soup.find("div", class_=re.compile(r"article|content|body", re.I)),
    ]

    best: Optional[Tag] = None
    best_len = -1

    for candidate in candidates:
        if isinstance(candidate, Tag):
            text_len = len(candidate.get_text(" ", strip=True))
            if text_len > best_len:
                best = candidate
                best_len = text_len

    return best if best is not None else soup


def _iter_fool_body_blocks(container: Tag, title: Optional[str]) -> list[str]:
    """
    Walk the page in order and keep likely article prose.
    This is more reliable for Fool than just grabbing all <p> tags because
    article text is interrupted by widgets and stock quote blocks.
    """
    blocks: list[str] = []
    seen: set[str] = set()
    started = False

    allowed_tags = {"p", "h2", "h3", "li"}

    for node in container.descendants:
        if isinstance(node, NavigableString):
            continue
        if not isinstance(node, Tag):
            continue
        if node.name not in allowed_tags:
            continue

        text = _clean_text(node.get_text(" ", strip=True))
        if not text:
            continue

        if title and text == title:
            started = True
            continue

        # Start once real content begins.
        if not started:
            if text == "Key Points":
                started = True
                continue
            if len(text) >= 60:
                started = True
            else:
                continue

        if text in _STOP_MARKERS:
            break

        if _is_noise_block(text):
            continue

        # Drop repetitive stock widget fragments
        if text in {"Apple", "Amazon", "Dutch Bros"} and len(text.split()) <= 3:
            continue

        if text not in seen:
            seen.add(text)
            blocks.append(text)

    return blocks


def _postprocess_fool_blocks(blocks: list[str]) -> list[str]:
    cleaned: list[str] = []
    for block in blocks:
        if len(block) < 25:
            continue
        if re.match(r"^Apr \d{1,2}, \d{4}", block):
            continue
        cleaned.append(block)

    return cleaned


def get_article_content(article_url: str) -> dict[str, Any]:
    _validate_url_host(article_url, ("fool.com", "www.fool.com"))

    response = _request(article_url)
    soup = BeautifulSoup(response.text, "html.parser")

    title = None
    h1 = soup.find("h1")
    if h1:
        title = _clean_text(h1.get_text(" ", strip=True))

    published_at = _extract_article_date(soup)

    container = _select_fool_article_container(soup)
    blocks = _iter_fool_body_blocks(container, title=title)
    blocks = _postprocess_fool_blocks(blocks)

    # Fallback to generic paragraph extraction if linear walk underperformed.
    if len(blocks) < 3:
        fallback_blocks: list[str] = []
        for node in container.find_all(["p", "li"]):
            text = _clean_text(node.get_text(" ", strip=True))
            if not text or _is_noise_block(text):
                continue
            if len(text) >= 25:
                fallback_blocks.append(text)
        blocks = _postprocess_fool_blocks(fallback_blocks)

    body = _normalize_article_text("\n\n".join(blocks))

    if not title:
        raise HTTPException(status_code=422, detail="Could not extract article title from Fool page.")
    if not body:
        raise HTTPException(
            status_code=422,
            detail="Could not extract article body from Fool page. The article structure may have changed.",
        )

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
# Core stock API function
# -----------------------------------------------------------------------------

def get_stock_data(
    symbol: str,
    since: Optional[str | date] = None,
    limit: int = DEFAULT_LIMIT,
    source_mode: SourceMode = SourceMode.quote_plus_search,
) -> list[dict[str, Any]]:
    symbol = symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol is required.")
    if limit < 0:
        raise HTTPException(status_code=400, detail="limit must be >= 0")

    since_date = _parse_since(since)

    lookup = _resolve_instrument(symbol)
    final_url = _resolve_quote_url(symbol, lookup)

    quote_response = _request(final_url)
    quote_html = quote_response.text
    quote_soup = BeautifulSoup(quote_html, "html.parser")

    company_name = lookup.get("company_name") or _extract_company_name(quote_soup)

    quote_articles, quote_transcripts = _collect_quote_page_links(quote_html)

    search_articles: list[LinkItem] = []
    search_transcripts: list[LinkItem] = []

    if source_mode == SourceMode.quote_plus_search:
        search_articles, search_transcripts = _search_fool_site_pages(
            symbol=symbol,
            company_name=company_name,
            max_pages=DEFAULT_SEARCH_PAGES,
        )

    articles = _dedupe_items(quote_articles + search_articles)
    transcripts = _dedupe_items(quote_transcripts + search_transcripts)

    articles = _apply_limit(_sort_items_newest_first(_filter_items_since(articles, since_date)), limit)
    transcripts = _apply_limit(_sort_items_newest_first(_filter_items_since(transcripts, since_date)), limit)

    payload = StockResponse(
        data=StockData(
            symbol=symbol,
            articles=articles,
            final_url=final_url,
            transcripts=transcripts,
            instrument_id=lookup.get("instrument_id"),
            analyst_opinions=_extract_analyst_opinion(quote_soup),
        ),
        status="success",
    )

    return [payload.model_dump()]


# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------

app = FastAPI(
    title="Fool.com Stock URL API",
    version="2.1.0",
    description=(
        "Returns Fool stock article URLs, transcript URLs, quote URL, and "
        "extracts article title/body/date from Fool article pages."
    ),
)


@app.get("/stocks/{symbol}", response_model=list[StockResponse])
def get_stock(
    symbol: str,
    since: Optional[str] = Query(None, description="Only include items on or after YYYY-MM-DD"),
    limit: int = Query(DEFAULT_LIMIT, ge=0, le=250, description="Maximum number of articles/transcripts"),
    source_mode: SourceMode = Query(
        SourceMode.quote_plus_search,
        description="quote_only or quote_plus_search",
    ),
):
    return get_stock_data(
        symbol=symbol,
        since=since,
        limit=limit,
        source_mode=source_mode,
    )


@app.get("/stock", response_model=list[StockResponse])
def get_stock_query(
    symbol: str = Query(..., min_length=1, description="Ticker symbol, e.g. AAPL"),
    since: Optional[str] = Query(None, description="Only include items on or after YYYY-MM-DD"),
    limit: int = Query(DEFAULT_LIMIT, ge=0, le=250, description="Maximum number of articles/transcripts"),
    source_mode: SourceMode = Query(
        SourceMode.quote_plus_search,
        description="quote_only or quote_plus_search",
    ),
):
    return get_stock_data(
        symbol=symbol,
        since=since,
        limit=limit,
        source_mode=source_mode,
    )


@app.get("/article", response_model=ArticleResponse)
def get_article(url: str = Query(..., description="Full Fool article URL")):
    return get_article_content(url)


@app.get("/articles/content", response_model=ArticleResponse)
def get_article_content_route(url: str = Query(..., description="Full Fool article URL")):
    return get_article_content(url)


if __name__ == "__main__":
    import json

    test_symbol = "AAPL"
    print(f"Testing get_stock_data('{test_symbol}')")
    stock_result = get_stock_data(test_symbol, limit=5)
    print(json.dumps(stock_result, indent=2))

    data = stock_result[0].get("data", {}) if stock_result else {}
    article_items = data.get("articles") or []
    if article_items:
        test_url = article_items[0]["url"]
        print(f"\nTesting get_article_content('{test_url}')")
        article_result = get_article_content(test_url)
        print(json.dumps(article_result, indent=2))