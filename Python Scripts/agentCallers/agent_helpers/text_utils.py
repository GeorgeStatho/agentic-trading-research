"""Article time windows, token budgeting and JSON extraction from model replies."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import re
from typing import Any


def parse_published_at(value: str | None) -> datetime | None:
    #clean string
    text = str(value or "").strip()
    if not text:
        return None

    # Normalize offsets like +0000/-0400 into ISO 8601 +00:00/-04:00.
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def normalize_time_window(
    *,
    start_time: datetime | None,
    end_time: datetime | None,
    max_age_days: int | None,
) -> tuple[datetime | None, datetime | None]:
    normalized_end = end_time.astimezone(timezone.utc) if end_time is not None else datetime.now(timezone.utc)
    normalized_start = start_time.astimezone(timezone.utc) if start_time is not None else None

    if normalized_start is None and max_age_days is not None:
        #gets how many days ago a article was from
        normalized_start = normalized_end - timedelta(days=max_age_days)

    return normalized_start, normalized_end


def published_at_in_window(
    published_at_text: str | None,
    *,
    start_time: datetime | None,
    end_time: datetime | None,
) -> bool:
    published_at = parse_published_at(published_at_text)
    if published_at is None:
        return False
    if start_time is not None and published_at < start_time:
        return False
    if end_time is not None and published_at > end_time:
        return False
    return True


def estimate_tokens(text: str) -> int:
    normalized = str(text or "")
    if not normalized:
        return 0
    #each 4 characters is about 1 token1, so the length of the string divided by 4 should help us estimate how much context a model needs to process
    return max(1, len(normalized) // 4)


def estimate_article_tokens(article: dict[str, Any]) -> int:
    combined = " ".join(
        [
            str(article.get("title") or ""),
            str(article.get("summary") or ""),
            str(article.get("body") or ""),
            str(article.get("source") or ""),
            str(article.get("source_url") or ""),
        ]
    )
    #combines article from database into one string block, then estimates the tokens of taht string
    return estimate_tokens(combined)


def build_token_limited_batches(
    articles: list[dict[str, Any]],
    *,
    context_limit: int,
    prompt_overhead_tokens: int,
) -> list[list[dict[str, Any]]]:
    # Assemble the token limited batches so callers can work from one normalized shape.
    usable_limit = max(1, int(context_limit) - int(prompt_overhead_tokens))
    batches: list[list[dict[str, Any]]] = []
    current_batch: list[dict[str, Any]] = []
    current_tokens = 0

    for article in articles:
        #gets the token amoutn a article is
        article_tokens = estimate_article_tokens(article)

        if article_tokens >= usable_limit:
            if current_batch:
                batches.append(current_batch)
                current_batch = []
                current_tokens = 0
            batches.append([article])
            #if current batch is holding a value, and a article is too big for the model, append that batch to the list of batches and begin a new one
            continue

        if current_batch and current_tokens + article_tokens > usable_limit:
            batches.append(current_batch)
            current_batch = [article]
            current_tokens = article_tokens
            #if the current batch made up of articles is full, appaend that to the total list of batches and amke a enw batch for teh article making the overflow
            continue

        current_batch.append(article)
        current_tokens += article_tokens

    if current_batch:
        batches.append(current_batch)

    return batches


def extract_json_value(text: str) -> Any:
    #clean text
    raw = str(text or "").strip()
    if not raw:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

        
    fence_start = raw.find("```")
    if fence_start >= 0:
        fence_end = raw.rfind("```")
        if fence_end > fence_start:
            fenced = raw[fence_start + 3:fence_end].strip()
            if fenced.lower().startswith("json"):
                fenced = fenced[4:].strip()
            try:
                return json.loads(fenced)
            except json.JSONDecodeError:
                pass

    for open_char, close_char in (("{", "}"), ("[", "]")):
        value_start = raw.find(open_char)
        value_end = raw.rfind(close_char)
        if value_start >= 0 and value_end > value_start:
            candidate = raw[value_start:value_end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    return None


def extract_json_object(text: str) -> dict[str, Any] | None:
    parsed = extract_json_value(text)
    if isinstance(parsed, dict):
        return parsed

    return None
