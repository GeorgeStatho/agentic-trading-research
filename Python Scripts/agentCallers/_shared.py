from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from typing import Any

try:
    from ollama import Client
except ImportError as exc:  # pragma: no cover - import guard for runtime setup
    raise RuntimeError(
        "The 'ollama' Python package is required for the agent callers. "
        "Install it in your environment with: pip install ollama"
    ) from exc


def get_ollama_client(host: str) -> Client:
    return Client(host=host)


def ask_ollama_model(
    client: Client,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0,
    host_label: str | None = None,
) -> str:
    try:
        response = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={
                "temperature": temperature,
            },
        )
    except Exception as exc:
        location = host_label or "the configured Ollama host"
        raise RuntimeError(
            "Could not complete the Ollama chat request. "
            f"Verify Ollama is running at {location} and that model '{model}' is installed."
        ) from exc

    message = response.get("message", {}) if hasattr(response, "get") else getattr(response, "message", None)
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Model returned empty content: {response}")

    return content


def parse_published_at(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None

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


def estimate_tokens(text: str) -> int:
    normalized = str(text or "")
    if not normalized:
        return 0
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
    return estimate_tokens(combined)


def build_token_limited_batches(
    articles: list[dict[str, Any]],
    *,
    context_limit: int,
    prompt_overhead_tokens: int,
) -> list[list[dict[str, Any]]]:
    usable_limit = max(1, int(context_limit) - int(prompt_overhead_tokens))
    batches: list[list[dict[str, Any]]] = []
    current_batch: list[dict[str, Any]] = []
    current_tokens = 0

    for article in articles:
        article_tokens = estimate_article_tokens(article)

        if article_tokens >= usable_limit:
            if current_batch:
                batches.append(current_batch)
                current_batch = []
                current_tokens = 0
            batches.append([article])
            continue

        if current_batch and current_tokens + article_tokens > usable_limit:
            batches.append(current_batch)
            current_batch = [article]
            current_tokens = article_tokens
            continue

        current_batch.append(article)
        current_tokens += article_tokens

    if current_batch:
        batches.append(current_batch)

    return batches


def extract_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
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
                parsed = json.loads(fenced)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

    object_start = raw.find("{")
    object_end = raw.rfind("}")
    if object_start >= 0 and object_end > object_start:
        candidate = raw[object_start:object_end + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None

    return None
