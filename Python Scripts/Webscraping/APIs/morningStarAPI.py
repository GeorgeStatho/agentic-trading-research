from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
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

BASE_URL = "https://www.morningstar.com"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 20
DEFAULT_LIMIT = 25

COMMON_EXCHANGES = [
    "xnas",
    "xnys",
    "xase",
    "arcx",
    "xotc",
    "bats",
    "xlon",
    "xtse",
    "xasx",
    "xfra",
    "xetra",
]

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


class LinkItem(BaseModel):
    url: str
    path: str
    title: str
    source: Optional[str] = None
    published_at: Optional[str] = None


class AnalystOpinion(BaseModel):
    fair_value: Optional[str] = None
    uncertainty: Optional[str] = None
    economic_moat: Optional[str] = None


class StockData(BaseModel):
    symbol: str
    exchange: Optional[str] = None
    company_name: Optional[str] = None
    final_url: str
    news_url: Optional[str] = None
    transcripts_url: Optional[str] = None
    articles: list[LinkItem] = Field(default_factory=list)
    transcripts: list[LinkItem] = Field(default_factory=list)
    analyst_opinion: AnalystOpinion = Field(default_factory=AnalystOpinion)


class StockResponse(BaseModel):
    data: StockData
    status: str = "success"


# -----------------------------------------------------------------------------
# Utilities
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
    cleaned = re.sub(r",\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M$", "", cleaned)
    cleaned = re.sub(r"\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M$", "", cleaned)

    for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"):
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
# Quote page resolution
# -----------------------------------------------------------------------------

def _looks_like_morningstar_quote_page(html: str, symbol: str) -> bool:
    text = html.lower()
    return (
        f" {symbol.lower()}" in text
        and "/quote" in text
        and "news" in text
    )


def _resolve_quote_url(symbol: str, exchange: Optional[str] = None) -> tuple[str, str]:
    exchanges_to_try = [exchange.lower()] if exchange else []
    exchanges_to_try.extend([ex for ex in COMMON_EXCHANGES if ex not in exchanges_to_try])

    symbol_lower = symbol.lower()

    for ex in exchanges_to_try:
        candidate = f"{BASE_URL}/stocks/{ex}/{symbol_lower}/quote"
        try:
            response = session.get(candidate, timeout=15, allow_redirects=True)
            if response.status_code == 200 and _looks_like_morningstar_quote_page(response.text, symbol):
                return response.url, ex
        except requests.RequestException:
            continue

    raise HTTPException(
        status_code=404,
        detail=f"Could not resolve Morningstar quote page for symbol '{symbol}'.",
    )


def _derive_tab_url(quote_url: str, tab: str) -> str:
    return re.sub(r"/quote/?$", f"/{tab}", quote_url)


# -----------------------------------------------------------------------------
# Parsers driven by quote-page structure
# -----------------------------------------------------------------------------

def _extract_company_name(soup: BeautifulSoup) -> Optional[str]:
    h1 = soup.find("h1")
    if not h1:
        return None
    text = _clean_text(h1.get_text(" ", strip=True))
    text = re.sub(r"\s+[A-Z.\-]{1,10}$", "", text)
    return text or None


def _extract_analyst_opinion(soup: BeautifulSoup) -> AnalystOpinion:
    text = soup.get_text(" ", strip=True)

    fair_value = None
    uncertainty = None
    economic_moat = None

    fair_value_match = re.search(
        r"Fair Value\s+\$([0-9][0-9,]*(?:\.\d+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if fair_value_match:
        fair_value = fair_value_match.group(1)

    uncertainty_match = re.search(
        r"Uncertainty\s+(Low|Medium|High|Very High)",
        text,
        flags=re.IGNORECASE,
    )
    if uncertainty_match:
        uncertainty = uncertainty_match.group(1).title()

    moat_match = re.search(
        r"Economic Moat\s+(None|Narrow|Wide)",
        text,
        flags=re.IGNORECASE,
    )
    if moat_match:
        economic_moat = moat_match.group(1).title()

    return AnalystOpinion(
        fair_value=fair_value,
        uncertainty=uncertainty,
        economic_moat=economic_moat,
    )


def _is_real_morningstar_article_url(url: str, path: str) -> bool:
    lower = path.lower()
    host = (urlparse(url).netloc or "").lower()

    # Allow external publisher article links from the quote page news section.
    if host and host not in {"www.morningstar.com", "morningstar.com"}:
        return True

    # Real Morningstar editorial/news pages
    if lower.startswith("/news/"):
        return True

    # Some deeper stock-specific article pages exist under /stocks/
    if lower.startswith("/stocks/"):
        if lower.endswith((
            "/quote",
            "/news",
            "/chart",
            "/financials",
            "/valuation",
            "/ownership",
            "/dividends",
            "/earnings-transcript",
            "/earnings-transcripts",
        )):
            return False

        if lower.count("/") >= 5:
            return True

    return False


def _split_title_source_date(text: str) -> tuple[str, Optional[str], Optional[str]]:
    cleaned = _clean_text(text)

    match = re.match(
        r"^(?P<title>.+?)\s+(?P<source>MarketWatch|Business Wire|Global News Select|PR Newswire|Dow Jones)\s+(?P<date>[A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4}(?:,\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)?)$",
        cleaned,
    )
    if match:
        return (
            _clean_text(match.group("title")),
            _clean_text(match.group("source")),
            _clean_text(match.group("date")),
        )

    return cleaned, None, None


def _collect_quote_page_news(html: str) -> list[LinkItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[LinkItem] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        raw_text = _clean_text(anchor.get_text(" ", strip=True))

        if not href or not raw_text:
            continue

        absolute_url = urljoin(BASE_URL, href)
        path = _normalize_path(absolute_url)

        if not _is_real_morningstar_article_url(absolute_url, path):
            continue

        title, source, published_at = _split_title_source_date(raw_text)

        items.append(
            LinkItem(
                url=absolute_url,
                path=path,
                title=title,
                source=source,
                published_at=published_at,
            )
        )

    return _dedupe_items(items)


def _is_real_transcript_item(url: str, path: str) -> bool:
    lower = path.lower()
    host = (urlparse(url).netloc or "").lower()

    if host and host not in {"www.morningstar.com", "morningstar.com"}:
        return False

    if lower.endswith("/earnings-transcript") or lower.endswith("/earnings-transcripts"):
        return False

    return (
        ("transcript" in lower or "earnings" in lower)
        and lower.count("/") >= 5
    )


def _collect_quote_page_transcripts(html: str) -> list[LinkItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[LinkItem] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        title = _clean_text(anchor.get_text(" ", strip=True))
        if not href or not title:
            continue

        absolute_url = urljoin(BASE_URL, href)
        path = _normalize_path(absolute_url)

        if not _is_real_transcript_item(absolute_url, path):
            continue

        items.append(LinkItem(url=absolute_url, path=path, title=title))

    return _dedupe_items(items)


# -----------------------------------------------------------------------------
# Core function
# -----------------------------------------------------------------------------

def get_stock_data(
    symbol: str,
    since: Optional[str | date] = None,
    limit: int = DEFAULT_LIMIT,
    exchange: Optional[str] = None,
    source_mode: SourceMode = SourceMode.quote_only,
) -> list[dict]:
    symbol = symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    if limit < 0:
        raise HTTPException(status_code=400, detail="limit must be >= 0")

    since_date = _parse_since(since)

    final_url, resolved_exchange = _resolve_quote_url(symbol, exchange=exchange)
    quote_html = _request(final_url).text
    quote_soup = BeautifulSoup(quote_html, "html.parser")

    company_name = _extract_company_name(quote_soup)
    analyst_opinion = _extract_analyst_opinion(quote_soup)

    articles = _collect_quote_page_news(quote_html)
    transcripts = _collect_quote_page_transcripts(quote_html)

    news_url = _derive_tab_url(final_url, "news")
    transcripts_url = _derive_tab_url(final_url, "earnings-transcript")

    articles = _apply_limit(_sort_items_newest_first(_filter_items_since(articles, since_date)), limit)
    transcripts = _apply_limit(_sort_items_newest_first(_filter_items_since(transcripts, since_date)), limit)

    payload = StockResponse(
        data=StockData(
            symbol=symbol,
            exchange=resolved_exchange.upper(),
            company_name=company_name,
            final_url=final_url,
            news_url=news_url,
            transcripts_url=transcripts_url,
            articles=articles,
            transcripts=transcripts,
            analyst_opinion=analyst_opinion,
        ),
        status="success",
    )

    return [payload.model_dump()]


# -----------------------------------------------------------------------------
# Article extraction
# -----------------------------------------------------------------------------

def _extract_title_from_page(soup: BeautifulSoup) -> Optional[str]:
    h1 = soup.find("h1")
    if h1:
        return _clean_text(h1.get_text(" ", strip=True))

    for attr_name, attr_value in [
        ("property", "og:title"),
        ("name", "twitter:title"),
    ]:
        tag = soup.find("meta", attrs={attr_name: attr_value})
        if tag and tag.get("content"):
            return _clean_text(tag["content"])

    if soup.title and soup.title.string:
        return _clean_text(soup.title.string)

    return None


def _extract_body_from_page(soup: BeautifulSoup) -> str:
    article_container = None
    candidates = [
        soup.find("article"),
        soup.find("main"),
        soup.find("section"),
        soup.find("div", class_=re.compile(r"article|content|body", re.I)),
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
        r"^ADVERTISEMENT$",
        r"^Continue reading$",
        r"^Read more:?$",
        r"^About the Author$",
        r"^Help Center$",
        r"^Terms of Use$",
        r"^Privacy Center$",
    ]

    for block in blocks:
        if any(re.search(pattern, block, flags=re.I) for pattern in noise_patterns):
            continue
        if len(block.strip()) < 20:
            continue
        filtered_blocks.append(block)

    body = _normalize_article_text("\n\n".join(filtered_blocks))
    if body:
        return body

    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        return _normalize_article_text(_clean_text(og_desc["content"]))

    return ""


def get_article_content(article_url: str) -> dict:
    _validate_url_host(article_url, ("morningstar.com", "www.morningstar.com"))

    parsed = urlparse(article_url)
    path = parsed.path.lower()

    if not (
        path.startswith("/news/")
        or (
            path.startswith("/stocks/")
            and not path.endswith((
                "/quote",
                "/news",
                "/earnings-transcript",
                "/earnings-transcripts",
            ))
        )
    ):
        raise HTTPException(
            status_code=400,
            detail="URL must be a Morningstar article/news page, not a navigation or product page.",
        )

    response = _request(article_url)
    soup = BeautifulSoup(response.text, "html.parser")

    title = _extract_title_from_page(soup)
    body = _extract_body_from_page(soup)
    published_at = _extract_article_date(soup)

    if not title:
        raise HTTPException(status_code=422, detail="Could not extract article title from Morningstar page.")
    if not body:
        raise HTTPException(status_code=422, detail="Could not extract article body from Morningstar page.")

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
    title="Morningstar Stock URL API",
    version="2.1.0",
    description="Returns Morningstar stock quote/news links and article content with stronger extraction.",
)


@app.get("/stocks/{symbol}", response_model=list[StockResponse])
def get_stock(
    symbol: str,
    since: Optional[str] = Query(None, description="Only include items on or after YYYY-MM-DD"),
    limit: int = Query(DEFAULT_LIMIT, ge=0, le=250),
    exchange: Optional[str] = Query(None, description="Optional exchange code like xnas or xnys"),
    source_mode: SourceMode = Query(SourceMode.quote_only),
):
    return get_stock_data(
        symbol=symbol,
        since=since,
        limit=limit,
        exchange=exchange,
        source_mode=source_mode,
    )


@app.get("/stock", response_model=list[StockResponse])
def get_stock_query(
    symbol: str = Query(..., min_length=1),
    since: Optional[str] = Query(None, description="Only include items on or after YYYY-MM-DD"),
    limit: int = Query(DEFAULT_LIMIT, ge=0, le=250),
    exchange: Optional[str] = Query(None, description="Optional exchange code like xnas or xnys"),
    source_mode: SourceMode = Query(SourceMode.quote_only),
):
    return get_stock_data(
        symbol=symbol,
        since=since,
        limit=limit,
        exchange=exchange,
        source_mode=source_mode,
    )


@app.get("/article", response_model=ArticleResponse)
def get_article(url: str = Query(..., description="Full Morningstar article URL")):
    return get_article_content(url)


@app.get("/articles/content", response_model=ArticleResponse)
def get_article_content_route(url: str = Query(..., description="Full Morningstar article URL")):
    return get_article_content(url)


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
        if item.get("path", "").startswith("/news/")
        or (
            item.get("path", "").startswith("/stocks/")
            and not item.get("path", "").endswith((
                "/quote",
                "/news",
                "/earnings-transcript",
                "/earnings-transcripts",
            ))
        )
    ]

    if real_articles:
        test_url = real_articles[0]["url"]
        print(f"\nTesting get_article_content('{test_url}')")
        article_result = get_article_content(test_url)
        print(json.dumps(article_result, indent=2))
    else:
        print("\nNo real Morningstar article URLs found to test.")