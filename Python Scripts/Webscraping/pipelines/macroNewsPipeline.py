"""Ingest the official US macroeconomic release calendar.

The pipeline deliberately consumes first-party release schedules instead of
scraping commercial calendar aggregators.  Each adapter normalizes its source
into the existing ``macro_events`` database contract, which keeps the market
regime gate and its callers independent of the upstream publisher.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from html import unescape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

WEBSCRAPING_DIR = Path(__file__).resolve().parents[1]
if str(WEBSCRAPING_DIR) not in sys.path:
    sys.path.append(str(WEBSCRAPING_DIR))

PROJECT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_DIR / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

from db_helpers import load_macro_events
from fetching.http_client import HttpClient, HttpFetchError


MACRO_EVENTS_JSON_PATH = DATA_DIR / "macro_events.json"
CALENDAR_TIMEZONE = "America/New_York"
US_CALENDAR_ZONE = ZoneInfo(CALENDAR_TIMEZONE)
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

BLS_RELEASE_SCHEDULE_URL = "https://www.bls.gov/schedule/news_release/"
BEA_RELEASE_SCHEDULE_URL = "https://apps.bea.gov/news/schedule/"
FEDERAL_RESERVE_FOMC_SCHEDULE_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"


def _fetch_text(url: str, timeout: int = 20) -> str:
    return HttpClient(timeout_seconds=timeout, headers=DEFAULT_HEADERS).get_text(url)


def _clean_text(value: object) -> str:
    return " ".join(unescape(str(value or "")).split())


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "event"


def _strip_tags(fragment: str) -> str:
    without_non_content = re.sub(r"<(?:script|style).*?</(?:script|style)>", " ", fragment, flags=re.I | re.S)
    return _clean_text(re.sub(r"<[^>]+>", " ", without_non_content))


def _extract_table_rows(html: str) -> list[list[str]]:
    """Extract visible cells from every HTML table without browser dependencies."""
    rows: list[list[str]] = []
    for row_html in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, flags=re.I | re.S):
        cells = [_strip_tags(cell) for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html, flags=re.I | re.S)]
        if cells:
            rows.append(cells)
    return rows


def _schedule_year(html: str, default: int) -> int:
    """Use an explicitly displayed calendar year when an official page has one."""
    text = _strip_tags(html)
    years = [int(value) for value in re.findall(r"\b(20\d{2})\b", text)]
    return years[0] if years else default


def _parse_date(value: str, *, default_year: int, prefer_range_end: bool = False) -> date | None:
    normalized = _clean_text(value).replace("–", "-")
    explicit = re.search(
        r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday,?\s+)?"
        r"([A-Z][a-z]+\s+\d{1,2},\s+20\d{2})\b",
        normalized,
    )
    if explicit:
        return datetime.strptime(explicit.group(1), "%B %d, %Y").date()

    iso = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", normalized)
    if iso:
        return datetime.strptime(iso.group(1), "%Y-%m-%d").date()

    numeric = re.search(r"\b(\d{1,2}/\d{1,2}/(?:20)?\d{2})\b", normalized)
    if numeric:
        raw = numeric.group(1)
        for format_string in ("%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(raw, format_string).date()
            except ValueError:
                continue

    month_day = re.search(r"\b([A-Z][a-z]+)\s+(\d{1,2})(?:\s*-\s*(\d{1,2}))?\b", normalized)
    if month_day:
        month, start_day, end_day = month_day.groups()
        day_value = end_day if prefer_range_end and end_day else start_day
        try:
            return datetime.strptime(f"{month} {day_value} {default_year}", "%B %d %Y").date()
        except ValueError:
            return None
    return None


def _parse_time(value: str, fallback: time) -> time:
    match = re.search(r"\b(\d{1,2}:\d{2})\s*([AaPp][Mm])\b", value)
    if match:
        return datetime.strptime(f"{match.group(1)} {match.group(2).upper()}", "%I:%M %p").time()
    military = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", value)
    if military:
        return time(int(military.group(1)), int(military.group(2)))
    return fallback


def _infer_category(event_name: str) -> str:
    lowered = event_name.lower()
    if any(term in lowered for term in ("employment", "payroll", "unemployment", "job opening", "labor")):
        return "labor"
    if any(term in lowered for term in ("consumer price", "producer price", "inflation", "pce", "cpi")):
        return "inflation"
    if any(term in lowered for term in ("fomc", "federal funds", "interest rate", "monetary policy")):
        return "rates"
    if any(term in lowered for term in ("gdp", "gross domestic product", "retail sales", "personal income", "industrial production")):
        return "growth"
    if any(term in lowered for term in ("trade", "exports", "imports", "current account")):
        return "trade"
    if any(term in lowered for term in ("petroleum", "natural gas", "electricity", "energy")):
        return "energy"
    return "macro"


def _importance_for(event_name: str, *, source: str) -> str:
    lowered = event_name.lower()
    if source == "Federal Reserve":
        return "high"
    high_impact = (
        "consumer price",
        "producer price",
        "employment situation",
        "payroll",
        "gross domestic product",
        "personal income and outlays",
        "pce",
    )
    if any(term in lowered for term in high_impact):
        return "high"
    return "medium"


@dataclass(frozen=True)
class OfficialCalendarAdapter:
    key: str
    source: str
    schedule_url: str
    default_release_time: time
    parser: Callable[[str, "OfficialCalendarAdapter", datetime], list[dict[str, Any]]]

    def fetch_events(self, *, now: datetime, fetch_text: Callable[[str], str]) -> list[dict[str, Any]]:
        return self.parser(fetch_text(self.schedule_url), self, now)


def _event_from_row(
    row: list[str],
    *,
    adapter: OfficialCalendarAdapter,
    default_year: int,
    prefer_range_end: bool = False,
) -> dict[str, Any] | None:
    row_text = " ".join(row)
    event_date = _parse_date(row_text, default_year=default_year, prefer_range_end=prefer_range_end)
    if event_date is None:
        return None

    ignored = {"release schedule", "date", "time", "release", "schedule", "time release"}
    name_candidates = [
        cell for cell in row if cell.strip().lower() not in ignored and _parse_date(cell, default_year=default_year) is None
    ]
    event_name = max(name_candidates, key=len, default="")
    if not event_name or event_name.lower() in {"release", "schedule"}:
        return None

    local_time = _parse_time(row_text, adapter.default_release_time)
    scheduled_at = datetime.combine(event_date, local_time, tzinfo=US_CALENDAR_ZONE)
    event_key = f"{adapter.key}::{event_date.isoformat()}::{local_time.isoformat()}::{_slugify(event_name)}"
    return {
        "source": adapter.source,
        "event_key": event_key,
        "event_name": event_name,
        "event_date": event_date.isoformat(),
        "event_time": local_time.strftime("%H:%M:%S"),
        "scheduled_at_utc": scheduled_at.astimezone(timezone.utc).isoformat(),
        "calendar_timezone": CALENDAR_TIMEZONE,
        "country": "United States",
        "currency": "USD",
        "category": _infer_category(event_name),
        "importance": _importance_for(event_name, source=adapter.source),
        "source_url": adapter.schedule_url,
    }


def _parse_release_schedule(html: str, adapter: OfficialCalendarAdapter, now: datetime) -> list[dict[str, Any]]:
    year = _schedule_year(html, now.year)
    events = [_event_from_row(row, adapter=adapter, default_year=year) for row in _extract_table_rows(html)]
    return [event for event in events if event is not None]


def _parse_fomc_schedule(html: str, adapter: OfficialCalendarAdapter, now: datetime) -> list[dict[str, Any]]:
    """Normalize FOMC meeting rows; policy decisions publish on the final meeting day."""
    year = _schedule_year(html, now.year)
    events: list[dict[str, Any]] = []
    for row in _extract_table_rows(html):
        row_text = " ".join(row)
        if not any(term in row_text.lower() for term in ("fomc", "federal open market", "meeting")):
            continue
        event = _event_from_row(row, adapter=adapter, default_year=year, prefer_range_end=True)
        if event is None:
            continue
        event["event_name"] = "FOMC Statement"
        event["category"] = "rates"
        event["importance"] = "high"
        event["event_key"] = (
            f"{adapter.key}::{event['event_date']}::{event['event_time']}::fomc-statement"
        )
        events.append(event)
    return events


OFFICIAL_CALENDAR_ADAPTERS: tuple[OfficialCalendarAdapter, ...] = (
    OfficialCalendarAdapter(
        key="bls",
        source="Bureau of Labor Statistics",
        schedule_url=BLS_RELEASE_SCHEDULE_URL,
        default_release_time=time(8, 30),
        parser=_parse_release_schedule,
    ),
    OfficialCalendarAdapter(
        key="bea",
        source="Bureau of Economic Analysis",
        schedule_url=BEA_RELEASE_SCHEDULE_URL,
        default_release_time=time(8, 30),
        parser=_parse_release_schedule,
    ),
    OfficialCalendarAdapter(
        key="federal-reserve",
        source="Federal Reserve",
        schedule_url=FEDERAL_RESERVE_FOMC_SCHEDULE_URL,
        default_release_time=time(14, 0),
        parser=_parse_fomc_schedule,
    ),
)


def _enabled_adapter_keys() -> set[str]:
    configured = os.getenv("MACRO_CALENDAR_SOURCES", "").strip()
    if not configured:
        return {adapter.key for adapter in OFFICIAL_CALENDAR_ADAPTERS}
    return {_slugify(item) for item in configured.split(",") if item.strip()}


def _normalize_calendar_timestamps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refreshed_at = datetime.now(timezone.utc).isoformat()
    for event in events:
        calendar_timezone = str(event.get("calendar_timezone") or CALENDAR_TIMEZONE)
        try:
            calendar_zone = ZoneInfo(calendar_timezone)
        except Exception:
            calendar_timezone = CALENDAR_TIMEZONE
            calendar_zone = US_CALENDAR_ZONE
        event["calendar_timezone"] = calendar_timezone
        event["calendar_refreshed_at"] = refreshed_at

        supplied_timestamp = str(event.get("scheduled_at_utc") or "").strip()
        if supplied_timestamp:
            try:
                supplied_at = datetime.fromisoformat(supplied_timestamp.replace("Z", "+00:00"))
                if supplied_at.tzinfo is None:
                    supplied_at = supplied_at.replace(tzinfo=calendar_zone)
                event["scheduled_at_utc"] = supplied_at.astimezone(timezone.utc).isoformat()
                continue
            except ValueError:
                pass

        event["scheduled_at_utc"] = None
        event_date = str(event.get("event_date") or "").strip()
        event_time = str(event.get("event_time") or "").strip()
        if not event_date or not event_time:
            continue
        try:
            local_time = datetime.strptime(event_time, "%H:%M:%S").time()
            local_date = datetime.strptime(event_date, "%Y-%m-%d").date()
        except ValueError:
            continue
        event["scheduled_at_utc"] = datetime.combine(local_date, local_time, tzinfo=calendar_zone).astimezone(timezone.utc).isoformat()
    return events


def _deduplicate_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for event in events:
        event_key = _clean_text(event.get("event_key"))
        if event_key:
            unique[event_key] = event
    return list(unique.values())


def fetch_macro_calendar(
    *,
    now: datetime | None = None,
    fetch_text: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch enabled first-party schedules, containing individual source failures."""
    observed_at = now or datetime.now(timezone.utc)
    enabled_keys = _enabled_adapter_keys()
    active_fetch_text = fetch_text or _fetch_text
    events: list[dict[str, Any]] = []
    for adapter in OFFICIAL_CALENDAR_ADAPTERS:
        if adapter.key not in enabled_keys:
            continue
        try:
            source_events = adapter.fetch_events(now=observed_at, fetch_text=active_fetch_text)
        except HttpFetchError as exc:
            print(f"Failed to fetch {adapter.source} macro schedule: {exc}")
            continue
        except (TypeError, ValueError) as exc:
            print(f"Failed to parse {adapter.source} macro schedule: {exc}")
            continue
        events.extend(source_events)
        print(f"Parsed {len(source_events)} macro events from {adapter.source}.")
    return _deduplicate_events(events)


def ingest_macro_and_news() -> dict[str, int]:
    macro_events = _normalize_calendar_timestamps(fetch_macro_calendar())
    load_macro_events(macro_events)
    MACRO_EVENTS_JSON_PATH.write_text(
        json.dumps(macro_events, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {"macro_events": len(macro_events), "news_articles": 0}


if __name__ == "__main__":
    try:
        print(json.dumps(ingest_macro_and_news(), indent=2, sort_keys=True))
    except KeyboardInterrupt:
        print("\nMacro pipeline interrupted by user.")
