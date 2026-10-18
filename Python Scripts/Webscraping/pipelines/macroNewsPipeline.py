from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from html import unescape
from pathlib import Path

import requests


PROJECT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_DIR / "Data"

from news_db_modules.macro import load_macro_events


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://calendar.example.com/economic-calendar/",
}
INVESTING_CALENDAR_URLS = [
    "https://calendar.example.com/economic-calendar/",
    "https://calendar.example.com/-economic-calendar",
    "https://calendar.example.com/economic-calendar-",
]
FOREX_FACTORY_CALENDAR_URL = "https://calendar-feed.example.com/ff_calendar_thisweek.json"
HTML_DEBUG_PREVIEW_CHARS = 2000
MACRO_EVENTS_JSON_PATH = DATA_DIR / "macro_events.json"
COUNTRY_BY_CURRENCY = {
    "AUD": "Australia",
    "BRL": "Brazil",
    "CAD": "Canada",
    "CHF": "Switzerland",
    "CNY": "China",
    "CNH": "China",
    "EUR": "Euro Area",
    "GBP": "United Kingdom",
    "HKD": "Hong Kong",
    "ILS": "Israel",
    "INR": "India",
    "JPY": "Japan",
    "KRW": "South Korea",
    "MXN": "Mexico",
    "NOK": "Norway",
    "NZD": "New Zealand",
    "PLN": "Poland",
    "RUB": "Russia",
    "SEK": "Sweden",
    "SGD": "Singapore",
    "TRY": "Turkey",
    "USD": "United States",
    "ZAR": "South Africa",
}
CALENDAR_TIMEZONE = "America/New_York"


def _fetch_text(url: str, timeout: int = 20) -> str:
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.text


def _fetch_json(url: str, timeout: int = 20) -> Any:
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.json()


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


def _extract_attr(fragment: str, attr_name: str) -> str | None:
    match = re.search(rf'{re.escape(attr_name)}="([^"]+)"', fragment, flags=re.I)
    if match:
        return _clean_text(match.group(1))
    return None


def _importance_from_cell(cell_html: str) -> str:
    stars = len(re.findall(r'grayFullBullishIcon|bullishIcon|importance', cell_html, flags=re.I))
    if stars >= 3:
        return "high"
    if stars == 2:
        return "medium"
    if stars == 1:
        return "low"
    return "normal"


def _infer_category(event_name: str) -> str:
    lowered = event_name.lower()
    if any(term in lowered for term in ["payroll", "employment", "unemployment", "labor", "job openings"]):
        return "labor"
    if any(term in lowered for term in ["consumer price", "producer price", "inflation", "ppi", "cpi", "pce"]):
        return "inflation"
    if any(term in lowered for term in ["interest rate", "fed", "ecb", "central bank"]):
        return "rates"
    if any(term in lowered for term in ["gdp", "gross domestic product", "retail sales", "industrial production"]):
        return "growth"
    if any(term in lowered for term in ["trade balance", "current account", "exports", "imports"]):
        return "trade"
    return "macro"


def _map_country(currency: str | None, raw_country: str | None = None) -> str | None:
    if currency:
        normalized_currency = currency.strip().upper()
        if normalized_currency in COUNTRY_BY_CURRENCY:
            return COUNTRY_BY_CURRENCY[normalized_currency]
        return normalized_currency
    return raw_country


def _parse_event_datetime(row_html: str, cell_text: str) -> tuple[str | None, str | None]:
    event_ts = _extract_attr(row_html, "data-event-datetime")
    if event_ts:
        normalized = event_ts.replace("/", "-")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(normalized, fmt)
                return parsed.date().isoformat(), parsed.time().strftime("%H:%M:%S")
            except ValueError:
                continue

    if cell_text:
        return None, cell_text
    return None, None


def _normalize_calendar_timestamps(events: list[dict]) -> list[dict]:
    refreshed_at = datetime.now(timezone.utc).isoformat()
    calendar_zone = ZoneInfo(CALENDAR_TIMEZONE)
    for event in events:
        event["calendar_timezone"] = CALENDAR_TIMEZONE
        event["calendar_refreshed_at"] = refreshed_at
        supplied_timestamp = str(event.get("scheduled_at_utc") or "").strip()
        supplied_at = None
        if supplied_timestamp:
            try:
                supplied_at = datetime.fromisoformat(supplied_timestamp.replace("Z", "+00:00"))
            except ValueError:
                supplied_at = None
        if supplied_at is not None:
            if supplied_at.tzinfo is None:
                supplied_at = supplied_at.replace(tzinfo=calendar_zone)
            event["scheduled_at_utc"] = supplied_at.astimezone(timezone.utc).isoformat()
            continue

        event["scheduled_at_utc"] = None
        event_date = str(event.get("event_date") or "").strip()
        event_time = str(event.get("event_time") or "").strip()
        if not event_date or not event_time or event_time.lower() in {"all day", "tentative", "-"}:
            continue
        parsed: datetime | None = None
        for date_format in ("%Y-%m-%d", "%A, %B %d, %Y"):
            for time_format in ("%H:%M:%S", "%H:%M", "%I:%M %p"):
                try:
                    parsed = datetime.strptime(f"{event_date} {event_time}", f"{date_format} {time_format}")
                    break
                except ValueError:
                    continue
            if parsed is not None:
                break
        if parsed is not None:
            event["scheduled_at_utc"] = parsed.replace(tzinfo=calendar_zone).astimezone(timezone.utc).isoformat()
    return events


def _extract_calendar_rows(html: str) -> list[str]:
    return re.findall(
        r'(<tr[^>]+id="eventRowId_[^"]+"[^>]*>.*?</tr>)',
        html,
        flags=re.S | re.I,
    )


def _extract_cell(row_html: str, class_name: str) -> str | None:
    match = re.search(
        rf'<td[^>]*class="[^"]*{re.escape(class_name)}[^"]*"[^>]*>(.*?)</td>',
        row_html,
        flags=re.S | re.I,
    )
    if not match:
        return None
    return match.group(1)


def _parse_calendar_row(row_html: str) -> dict | None:
    # Parse the calendar row into a structure the rest of the helper flow can use directly.
    event_id = _extract_attr(row_html, "event_attr_ID")
    raw_country = _extract_attr(row_html, "data-country")

    time_html = _extract_cell(row_html, "time")
    currency_html = _extract_cell(row_html, "flagCur")
    sentiment_html = _extract_cell(row_html, "sentiment")
    event_html = _extract_cell(row_html, "event")
    actual_html = _extract_cell(row_html, "act")
    forecast_html = _extract_cell(row_html, "fore")
    previous_html = _extract_cell(row_html, "prev")

    event_name = _strip_tags(event_html or "")
    if not event_name:
        return None

    currency = _strip_tags(currency_html or "")
    country = _map_country(currency, raw_country)
    event_date, event_time = _parse_event_datetime(row_html, _strip_tags(time_html or ""))
    importance = _importance_from_cell(sentiment_html or "")
    actual = _strip_tags(actual_html or "")
    forecast = _strip_tags(forecast_html or "")
    previous = _strip_tags(previous_html or "")

    article_key = event_id or f"{event_date or 'unknown-date'}::{event_time or 'unknown-time'}::{_slugify(event_name)}"
    return {
        "source": "calendar.example.com",
        "event_key": f"investing::{article_key}",
        "event_name": event_name,
        "event_date": event_date,
        "event_time": event_time,
        "country": country,
        "category": _infer_category(event_name),
        "importance": importance,
        "source_url": INVESTING_CALENDAR_URLS[0],
        "actual": actual or None,
        "forecast": forecast or None,
        "previous": previous or None,
        "currency": currency or None,
    }


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
    return bool(
        re.search(
            r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday),?\s+[a-z]+\s+\d{1,2},\s+\d{4}",
            value,
            flags=re.I,
        )
    )


def _parse_generic_table_calendar(html: str, source_url: str) -> list[dict]:
    # Parse the generic table calendar into a structure the rest of the helper flow can use directly.
    rows = _extract_table_rows(html)
    if not rows:
        return []

    events: list[dict] = []
    current_date: str | None = None

    for row in rows:
        if len(row) == 1 and _looks_like_date_heading(row[0]):
            current_date = row[0]
            continue

        if len(row) < 7:
            continue

        time_value, currency, _, event_name, actual, forecast, previous = row[:7]
        if event_name.lower() == "event" or time_value.lower() == "time":
            continue
        if not event_name:
            continue

        mapped_country = _map_country(currency)

        event_key = f"investing::{_slugify(current_date or 'unknown-date')}::{_slugify(time_value or 'unknown-time')}::{_slugify(event_name)}"
        events.append(
            {
                "source": "calendar.example.com",
                "event_key": event_key,
                "event_name": event_name,
                "event_date": current_date,
                "event_time": time_value or None,
                "country": mapped_country,
                "category": _infer_category(event_name),
                "importance": "normal",
                "source_url": source_url,
                "actual": actual or None,
                "forecast": forecast or None,
                "previous": previous or None,
                "currency": currency or None,
            }
        )

    return events


def _forex_factory_importance(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"high", "medium", "low"}:
        return normalized
    return "normal"


def _parse_forex_factory_calendar(payload: Any) -> list[dict]:
    if not isinstance(payload, list):
        return []

    events: list[dict] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        event_name = _clean_text(str(row.get("title") or ""))
        currency = _clean_text(str(row.get("country") or "")).upper()
        scheduled_text = str(row.get("date") or "").strip()
        if not event_name or not currency or not scheduled_text:
            continue
        try:
            scheduled_at = datetime.fromisoformat(scheduled_text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=ZoneInfo(CALENDAR_TIMEZONE))

        local_scheduled_at = scheduled_at.astimezone(ZoneInfo(CALENDAR_TIMEZONE))
        event_key = "::".join(
            ("forex-factory", currency, local_scheduled_at.isoformat(), _slugify(event_name))
        )
        events.append(
            {
                "source": "Forex Factory",
                "event_key": event_key,
                "event_name": event_name,
                "event_date": local_scheduled_at.date().isoformat(),
                "event_time": local_scheduled_at.time().strftime("%H:%M:%S"),
                "scheduled_at_utc": scheduled_at.astimezone(timezone.utc).isoformat(),
                "country": _map_country(currency),
                "currency": currency,
                "category": _infer_category(event_name),
                "importance": _forex_factory_importance(row.get("impact")),
                "source_url": FOREX_FACTORY_CALENDAR_URL,
                "actual": _clean_text(str(row.get("actual") or "")) or None,
                "forecast": _clean_text(str(row.get("forecast") or "")) or None,
                "previous": _clean_text(str(row.get("previous") or "")) or None,
            }
        )
    return events


def fetch_forex_factory_macro_calendar() -> list[dict]:
    try:
        events = _parse_forex_factory_calendar(_fetch_json(FOREX_FACTORY_CALENDAR_URL))
    except requests.RequestException as exc:
        print(f"Failed to fetch Forex Factory calendar: {exc}")
        return []
    if events:
        print(f"Parsed {len(events)} calendar events from Forex Factory.")
    else:
        print("No calendar events were parsed from Forex Factory.")
    return events


def fetch_investing_macro_calendar() -> list[dict]:
    # Fetch the investing macro calendar and normalize the result for the next stage.
    for url in INVESTING_CALENDAR_URLS:
        try:
            html = _fetch_text(url)
        except requests.RequestException as exc:
            print(f"Failed to fetch calendar.example.com calendar from {url}: {exc}")
            continue

        print(f"Fetched calendar.example.com HTML preview from {url}:")
        print(html[:HTML_DEBUG_PREVIEW_CHARS])

        events: list[dict] = []
        for row_html in _extract_calendar_rows(html):
            event = _parse_calendar_row(row_html)
            if event is not None:
                event["source_url"] = url
                events.append(event)

        if not events:
            events = _parse_generic_table_calendar(html, url)

        if events:
            print(f"Parsed {len(events)} calendar events from {url}.")
            return events

        print(f"No calendar events were parsed from {url}.")

    return []


def fetch_macro_calendar() -> list[dict]:
    """Fetch a dated macro calendar from the keyless JSON source first.

    calendar.example.com's HTML endpoint is retained as a fallback for compatibility,
    but it is commonly protected by bot mitigation and should not be the normal
    critical path for a fail-closed trading gate.
    """
    events = fetch_forex_factory_macro_calendar()
    return events or fetch_investing_macro_calendar()


def ingest_macro_and_news() -> dict[str, int]:
    macro_events = _normalize_calendar_timestamps(fetch_macro_calendar())
    load_macro_events(macro_events)
    MACRO_EVENTS_JSON_PATH.write_text(
        json.dumps(macro_events, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "macro_events": len(macro_events),
        "news_articles": 0,
    }


if __name__ == "__main__":
    try:
        summary = ingest_macro_and_news()
        print(json.dumps(summary, indent=2, sort_keys=True))
    except KeyboardInterrupt:
        print("\nMacro pipeline interrupted by user.")
