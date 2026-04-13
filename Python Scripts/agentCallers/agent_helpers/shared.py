from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import os
import re
from typing import Any

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover - optional provider dependency
    genai = None
    genai_types = None

try:
    from ollama import Client as OllamaClient
except ImportError:  # pragma: no cover - optional provider dependency
    OllamaClient = None


@dataclass(slots=True)
class Client:
    provider: str
    raw_client: Any
    host: str | None = None
    label: str | None = None


VERTEX_MODEL_ALIASES: dict[str, str] = {
    "llama3.1": "gemini-2.5-flash",
    "world-news-sectors": "gemini-2.5-flash",
}


def _normalize_provider(provider: str | None = None) -> str:
    return str(provider or os.getenv("LLM_PROVIDER", "ollama")).strip().lower()


def _build_vertex_client() -> Any:
    if genai is None:
        raise RuntimeError(
            "The 'google-genai' Python package is required for Vertex AI requests. "
            "Install it in your environment with: pip install google-genai"
        )

    project = str(
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("VERTEX_PROJECT")
        or os.getenv("GCP_PROJECT")
        or ""
    ).strip()
    location = str(
        os.getenv("GOOGLE_CLOUD_LOCATION")
        or os.getenv("VERTEX_LOCATION")
        or "global"
    ).strip()

    if not project:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT must be set when LLM_PROVIDER=vertex."
        )

    return genai.Client(vertexai=True, project=project, location=location)


def get_model_client(label: str | None = None) -> Client:
    provider = _normalize_provider()

    if provider == "vertex":
        return Client(provider=provider, raw_client=_build_vertex_client(), label=label)

    if provider != "ollama":
        raise RuntimeError(
            f"Unsupported LLM_PROVIDER '{provider}'. Expected 'ollama' or 'vertex'."
        )

    if OllamaClient is None:
        raise RuntimeError(
            "The 'ollama' Python package is required for Ollama requests. "
            "Install it in your environment with: pip install ollama"
        )

    return Client(
        provider=provider,
        raw_client=OllamaClient(host=label),
        host=label,
        label=label,
    )


def get_ollama_client(host: str) -> Client:
    # Backward-compatible wrapper while stages migrate to get_model_client().
    return get_model_client(host)


def _resolve_model_name(provider: str, model: str) -> str:
    normalized = str(model or "").strip()
    if provider != "vertex":
        return normalized

    if not normalized:
        return str(os.getenv("VERTEX_DEFAULT_MODEL", "gemini-2.5-flash")).strip()

    return VERTEX_MODEL_ALIASES.get(normalized, normalized)


def _extract_response_text(response: Any) -> str | None:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text

    candidates = getattr(response, "candidates", None)
    if not isinstance(candidates, list):
        return None

    parts: list[str] = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        raw_parts = getattr(content, "parts", None)
        if not isinstance(raw_parts, list):
            continue
        for part in raw_parts:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text.strip():
                parts.append(part_text)

    combined = "\n".join(parts).strip()
    return combined or None


def _ask_vertex_model(
    client: Client,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    response_schema: dict[str, Any] | None,
) -> str:
    if genai_types is None:
        raise RuntimeError(
            "The 'google-genai' Python package is required for Vertex AI requests."
        )

    config_kwargs: dict[str, Any] = {
        "temperature": temperature,
        "system_instruction": system_prompt,
    }
    if response_schema is not None:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_schema"] = response_schema

    try:
        response = client.raw_client.models.generate_content(
            model=_resolve_model_name(client.provider, model),
            contents=user_prompt,
            config=genai_types.GenerateContentConfig(**config_kwargs),
        )
    except Exception as exc:
        raise RuntimeError(
            "Could not complete the Vertex AI request. "
            "Verify Application Default Credentials are available and the model name is valid."
        ) from exc

    content = _extract_response_text(response)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Model returned empty content: {response}")

    return content


def ask_llm_model(
    client: Client,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0,
    host_label: str | None = None,
    response_schema: dict[str, Any] | None = None,
) -> str:
    if client.provider == "vertex":
        return _ask_vertex_model(
            client,
            model,
            system_prompt,
            user_prompt,
            temperature=temperature,
            response_schema=response_schema,
        )

    if client.provider != "ollama":
        raise RuntimeError(f"Unsupported model provider: {client.provider}")

    chat_kwargs: dict[str, Any] = {
        #chooses what llm model to use
        "model": _resolve_model_name(client.provider, model),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {
            "temperature": temperature,
        },
    }
    #if there is a response schema for the llm, use it
    if response_schema is not None:
        chat_kwargs["format"] = response_schema

    try:
        response = client.raw_client.chat(**chat_kwargs)
    except Exception as exc:
        location = host_label or client.label or "the configured Ollama host"
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


def ask_ollama_model(
    client: Client,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0,
    host_label: str | None = None,
    response_schema: dict[str, Any] | None = None,
) -> str:
    # Backward-compatible wrapper while stages migrate to ask_llm_model().
    return ask_llm_model(
        client,
        model,
        system_prompt,
        user_prompt,
        temperature=temperature,
        host_label=host_label,
        response_schema=response_schema,
    )


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
