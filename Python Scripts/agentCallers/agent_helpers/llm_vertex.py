"""Vertex AI and Gemini API clients, model-name aliases, the explicit prompt cache and the retrying call."""
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import TYPE_CHECKING, Any

from services.core.gemini_models import default_vertex_model, temperature_for, thinking_level_for, uses_thinking_levels
from services.core.settings import get_settings
from agent_helpers.llm_errors import (PROVIDER_DISPLAY_NAMES, ModelQuotaExhausted, ModelUnavailable,
                                      _build_vertex_error_message, _is_model_unavailable, _is_overloaded,
                                      _is_retryable, _is_vertex_cache_error, _is_vertex_rate_limited, _retry_reason)
from agent_helpers.llm_usage import _extract_response_text, _record_vertex_response_diagnostic

if TYPE_CHECKING:
    from agent_helpers.llm_client import Client

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover - optional provider dependency
    genai = None
    genai_types = None

try:
    from google.auth import default as google_auth_default
except ImportError:  # pragma: no cover - optional provider dependency
    google_auth_default = None

LOGGER = logging.getLogger(__name__)



# Both reach Gemini through the google-genai SDK: Vertex with Cloud credentials, gemini with an AI Studio key.
GOOGLE_GENAI_PROVIDERS: frozenset[str] = frozenset({"vertex", "gemini"})


# Ollama-era model names that resolve to VERTEX_DEFAULT_MODEL on Vertex and the Gemini API.
VERTEX_DEFAULT_MODEL_ALIASES: frozenset[str] = frozenset({"llama3.1", "world-news-sectors"})


VERTEX_AUTH_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/cloud-platform",
)


# cache_key -> (cached_content_name, local_expiry_monotonic)
_VERTEX_CACHE_REGISTRY: dict[str, tuple[str, float]] = {}


# cache_keys whose prompt the provider refused to cache; never retried.
_VERTEX_CACHE_UNAVAILABLE: set[str] = set()


def _build_vertex_client() -> Any:
    # Build the shared Vertex client lazily so downstream model calls do not have to duplicate provider setup.
    if genai is None:
        raise RuntimeError(
            "The 'google-genai' Python package is required for Vertex AI requests. "
            "Install it in your environment with: pip install google-genai"
        )
    if google_auth_default is None:
        raise RuntimeError(
            "The 'google-auth' Python package is required for Vertex AI requests. "
            "Install it in your environment with: pip install google-auth"
        )

    adc_credentials, detected_project = google_auth_default(scopes=VERTEX_AUTH_SCOPES)

    project = str(
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("VERTEX_PROJECT")
        or os.getenv("GCP_PROJECT")
        or detected_project
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

    return genai.Client(
        vertexai=True,
        project=project,
        location=location,
        credentials=adc_credentials,
    )


def _build_gemini_client() -> Any:
    if genai is None:
        raise RuntimeError(
            "The 'google-genai' Python package is required for Gemini API requests. "
            "Install it in your environment with: pip install google-genai"
        )

    # Read here rather than from settings: the settings object is recorded in the pipeline manifest.
    api_key = str(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY must be set when a stage uses the 'gemini' provider.")

    # Explicit: docker-compose sets GOOGLE_GENAI_USE_VERTEXAI=true, which would otherwise route this key to Vertex.
    return genai.Client(vertexai=False, api_key=api_key)


def _resolve_model_name(provider: str, model: str) -> str:
    normalized = str(model or "").strip()
    if provider not in GOOGLE_GENAI_PROVIDERS:
        return normalized

    if not normalized or normalized in VERTEX_DEFAULT_MODEL_ALIASES:
        return default_vertex_model()

    return normalized


def _cached_system_prompt_name(
    client: Client,
    resolved_model: str,
    system_prompt: str,
) -> str | None:
    """Return a reusable Vertex cache holding ``system_prompt``, or None if caching is unavailable.

    Only the system instruction is cached: it is the sole request component that is
    byte-identical across calls within a stage. Callers must fall back to sending the
    system prompt inline whenever this returns None -- prompts below the provider's
    minimum cacheable size (2048 tokens for Gemini 2.5) are rejected at create time.
    """
    if not get_settings().vertex.explicit_cache or genai_types is None:
        return None

    prompt_digest = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]
    cache_key = f"{resolved_model}:{prompt_digest}"

    cached = _VERTEX_CACHE_REGISTRY.get(cache_key)
    if cached is not None and time.monotonic() < cached[1]:
        return cached[0]

    if cache_key in _VERTEX_CACHE_UNAVAILABLE:
        return None

    try:
        cache = client.raw_client.caches.create(
            model=resolved_model,
            config=genai_types.CreateCachedContentConfig(
                system_instruction=system_prompt,
                display_name=f"agent-stage-{prompt_digest}",
                ttl=f"{get_settings().vertex.cache_ttl_seconds}s",
            ),
        )
    except Exception as exc:
        # Most commonly the prompt is under the provider minimum, which will never
        # succeed for this prompt. Record it so every later call skips the attempt.
        _VERTEX_CACHE_UNAVAILABLE.add(cache_key)
        LOGGER.info(
            "Vertex context caching unavailable for model '%s' (prompt %s); "
            "falling back to inline system instructions. Reason: %s",
            resolved_model,
            prompt_digest,
            exc,
        )
        return None

    # Expire locally before the server does so a call never races a vanishing cache.
    local_expiry = time.monotonic() + max(30, get_settings().vertex.cache_ttl_seconds - 60)
    _VERTEX_CACHE_REGISTRY[cache_key] = (cache.name, local_expiry)
    LOGGER.info(
        "Created Vertex context cache %s for model '%s' (prompt %s, ttl %ss)",
        cache.name,
        resolved_model,
        prompt_digest,
        get_settings().vertex.cache_ttl_seconds,
    )
    return cache.name


def _invalidate_vertex_cache(cache_name: str) -> None:
    for key, (name, _expiry) in list(_VERTEX_CACHE_REGISTRY.items()):
        if name == cache_name:
            _VERTEX_CACHE_REGISTRY.pop(key, None)


def ask_vertex_model(
    client: Client,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    response_schema: dict[str, Any] | None,
    response_diagnostic: dict[str, Any] | None,
    thinking_level: str | None = None,
    thinking_budget: int | None = None,
    max_output_tokens: int | None = None,
    timeout: float | None = None,
    retry: bool = True,
) -> str:
    # Call the Vertex model with the shared request settings and translate provider errors into a stable message shape.
    if genai_types is None:
        raise RuntimeError(
            "The 'google-genai' Python package is required for Vertex AI requests."
        )

    resolved_model = _resolve_model_name(client.provider, model)
    vertex_settings = get_settings().vertex

    config_kwargs: dict[str, Any] = {
        "temperature": temperature_for(resolved_model, temperature, vertex_settings.gemini3_temperature),
    }
    # Gemini 2.5 keeps its default thinking budget unless one is passed; it does not accept a thinking level.
    level = thinking_level_for(resolved_model, thinking_level or vertex_settings.thinking_level)
    if level and uses_thinking_levels(resolved_model):
        config_kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_level=level.upper())
    elif thinking_budget is not None and not uses_thinking_levels(resolved_model):
        config_kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_budget=thinking_budget)
    if max_output_tokens is not None:
        config_kwargs["max_output_tokens"] = max_output_tokens
    if response_schema is not None:
        config_kwargs["response_mime_type"] = "application/json"
        # The Gemini API's response_schema rejects additionalProperties, which the stage schemas use;
        # its JSON Schema field accepts them. Vertex keeps the field production was validated with.
        schema_field = "response_json_schema" if client.provider == "gemini" else "response_schema"
        config_kwargs[schema_field] = response_schema
    if timeout is not None or not retry:
        # Per request, so the shared client's own settings stay as they are for every other stage.
        config_kwargs["http_options"] = genai_types.HttpOptions(
            timeout=max(1, int(timeout * 1000)) if timeout is not None else None,
            retry_options=None if retry else genai_types.HttpRetryOptions(attempts=1),
        )

    # A cached system instruction is baked into the cache, so it must not also be sent
    # inline -- the provider rejects requests that set both. Creating a cache is a request of its own,
    # so a single-attempt call never does.
    cache_name = _cached_system_prompt_name(client, resolved_model, system_prompt) if retry else None
    if cache_name is not None:
        config_kwargs["cached_content"] = cache_name
    else:
        config_kwargs["system_instruction"] = system_prompt

    retry_delay_seconds = get_settings().vertex.retry_initial_delay_seconds
    max_attempts = get_settings().vertex.max_attempts if retry else 1

    for attempt in range(1, max_attempts + 1):
        try:
            response = client.raw_client.models.generate_content(
                model=resolved_model,
                contents=user_prompt,
                config=genai_types.GenerateContentConfig(**config_kwargs),
            )
            _record_vertex_response_diagnostic(
                response,
                provider=client.provider,
                model=resolved_model,
                response_diagnostic=response_diagnostic,
            )
            break
        except Exception as exc:
            # A cache can expire server-side mid-cycle. Drop it and retry inline rather
            # than failing the stage over a cost optimisation.
            if "cached_content" in config_kwargs and _is_vertex_cache_error(exc):
                LOGGER.warning(
                    "Vertex context cache %s is no longer usable; "
                    "retrying with an inline system instruction. Reason: %s",
                    config_kwargs["cached_content"],
                    exc,
                )
                _invalidate_vertex_cache(config_kwargs.pop("cached_content"))
                config_kwargs["system_instruction"] = system_prompt
                continue

            if _is_retryable(exc) and attempt < max_attempts:
                LOGGER.warning(
                    "%s %s for model '%s' (attempt %s/%s). "
                    "Sleeping %.1fs before retry.",
                    PROVIDER_DISPLAY_NAMES[client.provider],
                    _retry_reason(exc),
                    resolved_model,
                    attempt,
                    max_attempts,
                    retry_delay_seconds,
                )
                time.sleep(retry_delay_seconds)
                retry_delay_seconds = min(
                    get_settings().vertex.retry_max_delay_seconds,
                    retry_delay_seconds * 2,
                )
                continue

            if _is_vertex_rate_limited(exc) or _is_overloaded(exc):
                error_type = ModelQuotaExhausted
            elif _is_model_unavailable(exc):
                error_type = ModelUnavailable
            else:
                error_type = RuntimeError
            raise error_type(
                _build_vertex_error_message(exc, resolved_model, client.provider)
            ) from exc

    content = _extract_response_text(response)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Model returned empty content: {response}")

    return content
