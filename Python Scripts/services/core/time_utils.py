from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """The current time as ISO text with an explicit +00:00 offset."""
    return utc_now().isoformat()


def parse_timestamp(value: Any) -> datetime | None:
    """Parse ISO text ("Z" allowed), keeping its offset; naive text is UTC. None when unparseable."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def parse_utc_timestamp(value: Any) -> datetime | None:
    """Like parse_timestamp, converted to UTC."""
    parsed = parse_timestamp(value)
    return parsed.astimezone(timezone.utc) if parsed is not None else None
