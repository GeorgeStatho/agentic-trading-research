from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from html import unescape
from pathlib import Path

import requests

WEBSCRAPING_DIR = Path(__file__).resolve().parents[1]
if str(WEBSCRAPING_DIR) not in sys.path:
    sys.path.append(str(WEBSCRAPING_DIR))

PROJECT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_DIR / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

from db_helpers import load_macro_events


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AgenticTradingResearchDemo/1.0; +https://example.com/)",
    "Accept-Language": "en-US,en;q=0.9",
}
DEMO_CALENDAR_URLS = [
    "https://example.com/demo/calendar",
    "https://example.org/demo/calendar",
]
MACRO_EVENTS_JSON_PATH = DATA_DIR / "macro_events.json"


def _fetch_text(url: str, timeout: int = 20) -> str:
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.text


def _clean_text(value: str) -> str:
    return " ".join(unescape(value).split())


def _strip_tags(fragment: str) -> str:
    fragment = re.sub(r"<script.*?</script>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<style.*?</style>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return _clean_text(fragment)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "item"


def _extract_table_rows(html: str) -> list[list[str]]:
    table_matches = re.findall(r"<table[^>]*>(.*?)</table>", html, flags=re.S | re.I)
    best_rows: list[list[str]] = []

    for table_html in table_matches:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.S | re.I)
        parsed_rows: list[list[str]] = []
        for row_html in rows:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.S | re.I)
            cleaned_cells = [_strip_tags(cell) for cell in cells]
            if cleaned_cells:
                parsed_rows.append(cleaned_cells)
        if len(parsed_rows) > len(best_rows):
            best_rows = parsed_rows

    return best_rows


def _looks_like_date_heading(value: str) -> bool:
    lowered = value.lower()
    return bool(
        re.search(r"\b\d{4}-\d{2}-\d{2}\b", lowered)
        or re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", lowered)
    )


def _infer_category(event_name: str) -> str:
    lowered = event_name.lower()
    if any(term in lowered for term in ("payroll", "employment", "unemployment", "labor")):
        return "labor"
    if any(term in lowered for term in ("inflation", "cpi", "ppi", "prices")):
        return "inflation"
    if any(term in lowered for term in ("rate", "central bank", "fed")):
        return "rates"
    if any(term in lowered for term in ("gdp", "retail sales", "industrial production", "growth")):
        return "growth"
    return "macro"


def _parse_generic_table_calendar(html: str, source_url: str) -> list[dict]:
    rows = _extract_table_rows(html)
    if not rows:
        return []

    events: list[dict] = []
    current_date: str | None = None

    for row in rows:
        if len(row) == 1 and _looks_like_date_heading(row[0]):
            current_date = row[0]
            continue

        if len(row) < 3:
            continue

        event_time = row[0] or None
        country = row[1] or None
        event_name = row[2]
        if not event_name or event_name.lower() in {"event", "release", "calendar"}:
            continue

        event_key = f"demo::{_slugify(current_date or 'undated')}::{_slugify(event_name)}"
        events.append(
            {
                "source": "Demo Macro Calendar",
                "event_key": event_key,
                "event_name": event_name,
                "event_date": current_date,
                "event_time": event_time,
                "country": country,
                "category": _infer_category(event_name),
                "importance": "medium",
                "source_url": source_url,
                "actual": row[3] if len(row) > 3 and row[3] else None,
                "forecast": row[4] if len(row) > 4 and row[4] else None,
                "previous": row[5] if len(row) > 5 and row[5] else None,
                "currency": None,
            }
        )

    return events


def fetch_investing_macro_calendar() -> list[dict]:
    """Compatibility wrapper around the public demo macro-calendar parser.

    The public repository keeps a harmless generic HTML-table workflow here.
    Real source-specific endpoints and parsing live in the private repo.
    """

    for url in DEMO_CALENDAR_URLS:
        try:
            html = _fetch_text(url)
        except Exception:
            continue

        events = _parse_generic_table_calendar(html, url)
        if events:
            return events

    return []


def ingest_macro_and_news() -> dict[str, int]:
    macro_events = fetch_investing_macro_calendar()
    MACRO_EVENTS_JSON_PATH.write_text(json.dumps(macro_events, indent=2), encoding="utf-8")
    return {
        "macro_events_fetched": len(macro_events),
        "macro_events_saved": len(macro_events),
    }


if __name__ == "__main__":
    summary = ingest_macro_and_news()
    print(summary)
