"""Primary company-event adapters for SEC EDGAR and investor-relations feeds."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from fetching.http_client import HttpClient, HttpFetchError


PROJECT_DIR = Path(__file__).resolve().parents[3]
CONFIG_DIR = PROJECT_DIR / "config"
IR_FEEDS_PATH = CONFIG_DIR / "company_ir_feeds.json"
LOGGER = logging.getLogger("stock_scraper.company_official")

SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_DAILY_INDEX_ROOT_URL = "https://www.sec.gov/Archives/edgar/daily-index"
SEC_ARCHIVES_ROOT_URL = "https://www.sec.gov/Archives"
SEC_DEFAULT_LOOKBACK_DAYS = 7
SEC_CACHE_TTL_SECONDS = 5 * 60
SEC_REQUEST_INTERVAL_SECONDS = 0.12
SEC_DEFAULT_FILING_FORMS = frozenset(
    {
        "8-K",
        "8-K/A",
        "10-K",
        "10-K/A",
        "10-Q",
        "10-Q/A",
        "20-F",
        "20-F/A",
        "6-K",
        "6-K/A",
        "4",
        "4/A",
        "SC 13D",
        "SC 13D/A",
        "SC 13G",
        "SC 13G/A",
    }
)
_SEC_TICKER_MAP_CACHE: tuple[float, dict[str, int]] | None = None
_SEC_DAILY_INDEX_CACHE: dict[str, tuple[float, str]] = {}
_SEC_DAILY_INDEX_FAILURE_CACHE: dict[str, float] = {}
_SEC_LAST_REQUEST_AT = 0.0


@dataclass(frozen=True)
class CompanyEvent:
    company_id: int
    source: str
    article_key: str
    title: str
    source_url: str
    source_page_url: str
    summary: str | None
    body: str
    published_at: str | None
    section: str
    raw_json: dict[str, Any]


def _clean_text(value: object) -> str:
    return " ".join(unescape(str(value or "")).split())


def _normalise_ticker(value: object) -> str:
    return _clean_text(value).upper().replace(".", "-")


def _configured_sec_forms() -> frozenset[str]:
    configured = os.getenv("SEC_FILING_FORMS", "").strip()
    if not configured:
        return SEC_DEFAULT_FILING_FORMS
    return frozenset(item.strip().upper() for item in configured.split(",") if item.strip())


def _sec_user_agent(value: str | None = None) -> str | None:
    return _clean_text(value or os.getenv("SEC_USER_AGENT")) or None


def _sec_client(user_agent: str) -> HttpClient:
    return HttpClient(
        timeout_seconds=20,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json,text/plain,text/html;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        },
    )


def _daily_index_url(day: date) -> str:
    quarter = ((day.month - 1) // 3) + 1
    return f"{SEC_DAILY_INDEX_ROOT_URL}/{day.year}/QTR{quarter}/master.{day:%Y%m%d}.idx"


def _parse_sec_ticker_map(payload: Any) -> dict[str, int]:
    entries = payload.values() if isinstance(payload, dict) else payload
    if not isinstance(entries, Iterable):
        return {}

    mapping: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ticker = _normalise_ticker(entry.get("ticker"))
        try:
            cik = int(entry.get("cik_str"))
        except (TypeError, ValueError):
            continue
        if ticker and cik > 0:
            mapping[ticker] = cik
    return mapping


def _cached_ticker_map(fetch_json: Callable[[str], Any]) -> dict[str, int]:
    global _SEC_TICKER_MAP_CACHE
    now_monotonic = time.monotonic()
    if _SEC_TICKER_MAP_CACHE is not None and _SEC_TICKER_MAP_CACHE[0] > now_monotonic:
        return _SEC_TICKER_MAP_CACHE[1]
    _throttle_sec_request()
    parsed = _parse_sec_ticker_map(fetch_json(SEC_TICKER_MAP_URL))
    _SEC_TICKER_MAP_CACHE = (now_monotonic + SEC_CACHE_TTL_SECONDS, parsed)
    return parsed


def _cached_daily_index(index_url: str, fetch_text: Callable[[str], str]) -> str:
    now_monotonic = time.monotonic()
    cached = _SEC_DAILY_INDEX_CACHE.get(index_url)
    if cached is not None and cached[0] > now_monotonic:
        return cached[1]
    if _SEC_DAILY_INDEX_FAILURE_CACHE.get(index_url, 0.0) > now_monotonic:
        raise HttpFetchError("Recently unavailable SEC daily index.")
    _throttle_sec_request()
    try:
        payload = fetch_text(index_url)
    except HttpFetchError:
        _SEC_DAILY_INDEX_FAILURE_CACHE[index_url] = now_monotonic + SEC_CACHE_TTL_SECONDS
        raise
    _SEC_DAILY_INDEX_CACHE[index_url] = (now_monotonic + SEC_CACHE_TTL_SECONDS, payload)
    return payload


def _throttle_sec_request() -> None:
    global _SEC_LAST_REQUEST_AT
    elapsed = time.monotonic() - _SEC_LAST_REQUEST_AT
    if elapsed < SEC_REQUEST_INTERVAL_SECONDS:
        time.sleep(SEC_REQUEST_INTERVAL_SECONDS - elapsed)
    _SEC_LAST_REQUEST_AT = time.monotonic()


def _tracked_company_ciks(
    companies: Iterable[dict[str, Any]], ticker_map: dict[str, int]
) -> dict[int, list[dict[str, Any]]]:
    tracked: dict[int, list[dict[str, Any]]] = {}
    for company in companies:
        ticker = _normalise_ticker(company.get("symbol"))
        cik = ticker_map.get(ticker)
        if cik is not None:
            tracked.setdefault(cik, []).append(company)
    return tracked


def _lookback_day_count(value: int | None) -> int:
    candidate = value if value is not None else os.getenv("SEC_FILING_LOOKBACK_DAYS", SEC_DEFAULT_LOOKBACK_DAYS)
    try:
        return max(1, int(candidate))
    except (TypeError, ValueError):
        return SEC_DEFAULT_LOOKBACK_DAYS


def _parse_daily_index(payload: str) -> list[dict[str, str]]:
    filings: list[dict[str, str]] = []
    in_rows = False
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if line.startswith("CIK|"):
            in_rows = True
            continue
        if not in_rows or "|" not in line:
            continue
        fields = [item.strip() for item in line.split("|")]
        if len(fields) != 5:
            continue
        cik, company_name, form_type, filed_at, filename = fields
        try:
            int(cik)
        except ValueError:
            continue
        if not filename.startswith("edgar/"):
            continue
        filings.append(
            {
                "cik": cik,
                "company_name": company_name,
                "form_type": form_type.upper(),
                "filed_at": filed_at,
                "filename": filename,
            }
        )
    return filings


def _filing_accession(filename: str) -> str:
    match = re.search(r"/(\d{10}-\d{2}-\d{6})-index\.html?$", filename, flags=re.I)
    if match:
        return match.group(1)
    match = re.search(r"/(\d{10}-\d{2}-\d{6})", filename)
    return match.group(1) if match else filename


def _edgar_event(company: dict[str, Any], filing: dict[str, str], index_url: str) -> CompanyEvent:
    symbol = _clean_text(company.get("symbol")).upper()
    company_name = _clean_text(company.get("name")) or filing["company_name"]
    form_type = filing["form_type"]
    filing_url = f"{SEC_ARCHIVES_ROOT_URL}/{filing['filename']}"
    accession = _filing_accession(filing["filename"])
    title = f"{symbol}: SEC Form {form_type} filed"
    summary = f"{company_name} filed SEC Form {form_type} on {filing['filed_at']}."
    return CompanyEvent(
        company_id=int(company["id"]),
        source="SEC EDGAR",
        article_key=f"sec-edgar::{accession}",
        title=title,
        source_url=filing_url,
        source_page_url=index_url,
        summary=summary,
        body=summary,
        published_at=filing["filed_at"] or None,
        section="company_filing",
        raw_json={
            "event_type": "sec_filing",
            "company_id": company["id"],
            "symbol": symbol,
            "cik": filing["cik"],
            "form_type": form_type,
            "accession_number": accession,
            "filing": filing,
            "index_url": index_url,
        },
    )


def fetch_sec_edgar_events(
    companies: Iterable[dict[str, Any]],
    *,
    now: datetime | None = None,
    lookback_days: int | None = None,
    user_agent: str | None = None,
    get_json: Callable[[str], Any] | None = None,
    get_text: Callable[[str], str] | None = None,
    pause_seconds: float = 0.12,
) -> list[CompanyEvent]:
    """Return high-signal EDGAR filings for all tracked companies with a CIK.

    EDGAR's daily index provides one bounded request per day.  This avoids a
    per-company submissions request for thousands of tracked symbols.
    """
    configured_user_agent = _sec_user_agent(user_agent)
    if configured_user_agent is None:
        LOGGER.warning("SEC EDGAR ingestion skipped: set SEC_USER_AGENT to a descriptive contact value.")
        return []

    client = None
    if get_json is None or get_text is None:
        client = _sec_client(configured_user_agent)
    fetch_json = get_json or client.get_json  # type: ignore[union-attr]
    fetch_text = get_text or client.get_text  # type: ignore[union-attr]

    try:
        ticker_map = (
            _cached_ticker_map(fetch_json)
            if get_json is None
            else _parse_sec_ticker_map(fetch_json(SEC_TICKER_MAP_URL))
        )
    except HttpFetchError as exc:
        LOGGER.warning("SEC EDGAR ticker map fetch failed: %s", exc)
        return []
    tracked_by_cik = _tracked_company_ciks(companies, ticker_map)
    if not tracked_by_cik:
        return []

    observed_at = now or datetime.now(timezone.utc)
    day_count = _lookback_day_count(lookback_days)
    accepted_forms = _configured_sec_forms()
    events: list[CompanyEvent] = []
    seen_accessions: set[str] = set()

    for offset in range(day_count):
        index_day = observed_at.date() - timedelta(days=offset)
        index_url = _daily_index_url(index_day)
        try:
            index_payload = _cached_daily_index(index_url, fetch_text) if get_text is None else fetch_text(index_url)
        except HttpFetchError as exc:
            LOGGER.info("SEC EDGAR daily index unavailable at %s: %s", index_url, exc)
            continue
        for filing in _parse_daily_index(index_payload):
            cik = int(filing["cik"])
            if cik not in tracked_by_cik or filing["form_type"] not in accepted_forms:
                continue
            accession = _filing_accession(filing["filename"])
            if accession in seen_accessions:
                continue
            seen_accessions.add(accession)
            events.extend(
                _edgar_event(company, filing, index_url)
                for company in tracked_by_cik[cik]
            )
        if get_text is not None and pause_seconds > 0 and offset + 1 < day_count:
            time.sleep(pause_seconds)
    return events


def _parse_company_raw_json(company: dict[str, Any]) -> dict[str, Any]:
    raw_json = company.get("raw_json")
    if isinstance(raw_json, dict):
        return raw_json
    if isinstance(raw_json, str):
        try:
            payload = json.loads(raw_json)
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _feed_urls_from_value(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    urls: list[str] = []
    for item in values:
        url = _clean_text(item)
        if url.startswith("https://") and url not in urls:
            urls.append(url)
    return urls


def load_company_ir_feeds(config_path: Path | str = IR_FEEDS_PATH) -> dict[str, list[str]]:
    """Load ticker-to-RSS mappings; malformed or absent local config is empty."""
    path = Path(config_path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = payload.get("companies", payload) if isinstance(payload, dict) else {}
    if not isinstance(entries, dict):
        return {}
    feeds: dict[str, list[str]] = {}
    for ticker, value in entries.items():
        mapping_value = value.get("feeds") if isinstance(value, dict) else value
        urls = _feed_urls_from_value(mapping_value)
        if urls:
            feeds[_normalise_ticker(ticker)] = urls
    return feeds


def get_company_ir_feed_urls(company: dict[str, Any], configured_feeds: dict[str, list[str]]) -> list[str]:
    ticker = _normalise_ticker(company.get("symbol"))
    urls = list(configured_feeds.get(ticker, []))
    raw_json = _parse_company_raw_json(company)
    for field in ("ir_rss_url", "ir_rss_urls", "investor_relations_rss", "investor_relations_rss_urls"):
        for url in _feed_urls_from_value(raw_json.get(field)):
            if url not in urls:
                urls.append(url)
    return urls


def _feed_text(element: ET.Element, names: tuple[str, ...]) -> str:
    for name in names:
        value = element.findtext(name)
        if value:
            return _clean_text(value)
    return ""


def _parse_feed_timestamp(value: str) -> str | None:
    normalized = _clean_text(value)
    if not normalized:
        return None
    try:
        parsed = parsedate_to_datetime(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def parse_company_ir_feed(company: dict[str, Any], feed_url: str, xml_payload: str) -> list[CompanyEvent]:
    """Normalize RSS or Atom items from one explicitly configured IR feed."""
    try:
        root = ET.fromstring(xml_payload)
    except ET.ParseError:
        return []

    entries = list(root.findall(".//item"))
    is_atom = not entries
    if is_atom:
        entries = list(root.findall(".//{*}entry"))

    symbol = _clean_text(company.get("symbol")).upper()
    events: list[CompanyEvent] = []
    seen_keys: set[str] = set()
    for entry in entries:
        if is_atom:
            title = _feed_text(entry, ("{*}title",))
            summary = _feed_text(entry, ("{*}summary", "{*}content"))
            identifier = _feed_text(entry, ("{*}id", "{*}guid"))
            published_at = _parse_feed_timestamp(_feed_text(entry, ("{*}updated", "{*}published")))
            link = ""
            for link_element in entry.findall("{*}link"):
                href = _clean_text(link_element.attrib.get("href"))
                if href:
                    link = href
                    break
        else:
            title = _feed_text(entry, ("title",))
            summary = _feed_text(entry, ("description", "{*}encoded", "{*}content"))
            identifier = _feed_text(entry, ("guid", "link"))
            published_at = _parse_feed_timestamp(_feed_text(entry, ("pubDate", "published", "updated")))
            link = _feed_text(entry, ("link",))

        target_url = link or (identifier if identifier.startswith(("https://", "http://", "/")) else "")
        source_url = urljoin(feed_url, target_url)
        stable_value = identifier or source_url or f"{title}::{published_at or ''}"
        key_suffix = re.sub(r"\s+", "-", stable_value.strip())[:500]
        article_key = f"company-ir::{symbol}::{key_suffix}"
        if not title or not source_url or article_key in seen_keys:
            continue
        seen_keys.add(article_key)
        body = summary or f"Investor-relations release from {symbol}: {title}."
        events.append(
            CompanyEvent(
                company_id=int(company["id"]),
                source="Company Investor Relations",
                article_key=article_key,
                title=title,
                source_url=source_url,
                source_page_url=feed_url,
                summary=summary or None,
                body=body,
                published_at=published_at,
                section="company_press_release",
                raw_json={
                    "event_type": "company_ir_release",
                    "company_id": company["id"],
                    "symbol": symbol,
                    "feed_url": feed_url,
                    "feed_identifier": identifier or None,
                },
            )
        )
    return events


def fetch_company_ir_events(
    companies: Iterable[dict[str, Any]],
    *,
    configured_feeds: dict[str, list[str]] | None = None,
    get_text: Callable[[str], str] | None = None,
) -> list[CompanyEvent]:
    feeds = configured_feeds if configured_feeds is not None else load_company_ir_feeds()
    fetch_text = get_text or HttpClient(timeout_seconds=20).get_text
    events: list[CompanyEvent] = []
    for company in companies:
        for feed_url in get_company_ir_feed_urls(company, feeds):
            try:
                events.extend(parse_company_ir_feed(company, feed_url, fetch_text(feed_url)))
            except HttpFetchError:
                continue
    return events
