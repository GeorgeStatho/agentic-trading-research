from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import logging
import os
import re
import time
from typing import Any
from services.gemini_models import default_vertex_model, temperature_for, thinking_level_for, uses_thinking_levels
from services.settings import get_settings

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

try:
    from ollama import Client as OllamaClient
except ImportError:  # pragma: no cover - optional provider dependency
    OllamaClient = None

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional provider dependency
    OpenAI = None


LOGGER = logging.getLogger(__name__)
LLAMACPP_TIMEOUT_SECONDS = 1800


@dataclass(slots=True)
class Client:
    provider: str
    raw_client: Any
    host: str | None = None
    label: str | None = None


class ModelQuotaExhausted(RuntimeError):
    """The provider kept refusing the call for rate, quota or capacity limits."""


@dataclass(frozen=True)
class ModelAnswer:
    text: str
    provider: str
    # The model asked, as resolved for the provider; model_version is what the provider reported, if anything.
    model: str
    model_version: str | None = None
    fallback: bool = False

    def record(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model,
                "model_version": self.model_version, "fallback": self.fallback}


# Both reach Gemini through the google-genai SDK: Vertex with Cloud credentials, gemini with an AI Studio key.
GOOGLE_GENAI_PROVIDERS: frozenset[str] = frozenset({"vertex", "gemini"})
PROVIDER_DISPLAY_NAMES: dict[str, str] = {"vertex": "Vertex AI", "gemini": "Gemini API"}
# Ollama-era model names that resolve to VERTEX_DEFAULT_MODEL on Vertex and the Gemini API.
VERTEX_DEFAULT_MODEL_ALIASES: frozenset[str] = frozenset({"llama3.1", "world-news-sectors"})
VERTEX_AUTH_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/cloud-platform",
)


# Explicit context caching is opt-in. Implicit caching is already on by default for
# Gemini 2.5 models, so enable this only for stages whose system prompt clears the
# provider minimum (2048 tokens) and which run many calls inside one TTL window.
# Cached tokens are billed for storage per hour, and RUN_INTERVAL_SECONDS defaults to
# three hours -- a TTL longer than one cycle's strategist/manager burst is billed idle.

# cache_key -> (cached_content_name, local_expiry_monotonic)
_VERTEX_CACHE_REGISTRY: dict[str, tuple[str, float]] = {}
# cache_keys whose prompt the provider refused to cache; never retried.
_VERTEX_CACHE_UNAVAILABLE: set[str] = set()


def _normalize_provider(provider: str | None = None) -> str:
    return str(provider or os.getenv("LLM_PROVIDER", "ollama")).strip().lower()


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


def _build_openai_client() -> Any:
    if OpenAI is None:
        raise RuntimeError(
            "The 'openai' Python package is required for OpenAI requests. "
            "Install it in your environment with: pip install openai"
        )

    api_key = str(os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY must be set when LLM_PROVIDER=openai.")

    return OpenAI(api_key=api_key)


def _build_openai_compatible_client() -> Any:
    if OpenAI is None:
        raise RuntimeError(
            "The 'openai' Python package is required for OpenAI-compatible requests. "
            "Install it in your environment with: pip install openai"
        )

    base_url = get_settings().openai_compatible.base_url
    api_key = str(os.getenv("OPENAI_COMPATIBLE_API_KEY") or "").strip()
    if not base_url or not api_key:
        raise RuntimeError(
            "OPENAI_COMPATIBLE_BASE_URL and OPENAI_COMPATIBLE_API_KEY must be set for the 'openai_compatible' provider."
        )

    # Quota errors are handled by the caller's fallback, so the SDK should not sit retrying them.
    return OpenAI(base_url=base_url, api_key=api_key, max_retries=0)


def _build_llamacpp_client() -> Any:
    if OpenAI is None:
        raise RuntimeError(
            "The 'openai' Python package is required for llama.cpp requests. "
            "Install it in your environment with: pip install openai"
        )
    # llama-server ignores the key unless it was started with --api-key. A local model can spend many
    # minutes on one long prompt, and a client retry would recompute it from scratch, so wait instead.
    return OpenAI(base_url=get_settings().llamacpp.base_url, api_key="local",
                  timeout=LLAMACPP_TIMEOUT_SECONDS, max_retries=0)


def get_model_client(label: str | None = None, provider: str | None = None) -> Client:
    # Choose the model backend once and return the callable/client pair the rest of the code should use.
    # A blank provider uses LLM_PROVIDER.
    provider = _normalize_provider(provider)

    if provider == "vertex":
        return Client(provider=provider, raw_client=_build_vertex_client(), label=label)

    if provider == "gemini":
        return Client(provider=provider, raw_client=_build_gemini_client(), label=label)

    if provider == "openai":
        return Client(provider=provider, raw_client=_build_openai_client(), label=label)

    if provider == "llamacpp":
        base_url = get_settings().llamacpp.base_url
        return Client(provider=provider, raw_client=_build_llamacpp_client(), host=base_url, label=base_url)

    if provider == "openai_compatible":
        base_url = get_settings().openai_compatible.base_url
        return Client(provider=provider, raw_client=_build_openai_compatible_client(), host=base_url, label=base_url)

    if provider != "ollama":
        raise RuntimeError(
            f"Unsupported LLM_PROVIDER '{provider}'. Expected 'ollama', 'openai', 'llamacpp', "
            "'openai_compatible', 'vertex', or 'gemini'."
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
    if provider not in GOOGLE_GENAI_PROVIDERS:
        return normalized

    if not normalized or normalized in VERTEX_DEFAULT_MODEL_ALIASES:
        return default_vertex_model()

    return normalized


def _extract_response_text(response: Any) -> str | None:
    # Extract the response text from the raw payload and return a stable value.
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


def _safe_response_value(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    return str(value)


def _record_vertex_response_diagnostic(
    response: Any,
    *,
    provider: str,
    model: str,
    response_diagnostic: dict[str, Any] | None,
) -> None:
    """Capture completion metadata without retaining the request payload."""
    if response_diagnostic is None:
        return

    candidates = getattr(response, "candidates", None)
    candidate = candidates[0] if isinstance(candidates, list) and candidates else None
    response_diagnostic.update(
        {
            "provider": provider,
            "model": model,
            "candidate_count": len(candidates) if isinstance(candidates, list) else 0,
            "finish_reason": _safe_response_value(getattr(candidate, "finish_reason", None)),
            "finish_message": _safe_response_value(getattr(candidate, "finish_message", None)),
        }
    )
    model_version = getattr(response, "model_version", None)
    if isinstance(model_version, str) and model_version:
        response_diagnostic["model_version"] = model_version
    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        for key in (
            "prompt_token_count",
            "candidates_token_count",
            "total_token_count",
            "cached_content_token_count",
        ):
            value = _safe_response_value(getattr(usage, key, None))
            if value is not None:
                response_diagnostic[key] = value


def build_model_response_diagnostic(
    raw_response: str,
    *,
    response_diagnostic: dict[str, Any] | None = None,
    excerpt_characters: int = 4_000,
) -> dict[str, Any]:
    """Return a bounded diagnostic for an invalid structured model response."""
    raw = str(raw_response or "")
    excerpt_limit = max(1, int(excerpt_characters))
    diagnostic: dict[str, Any] = {
        "response_characters": len(raw),
        "response_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "response_excerpt": raw[:excerpt_limit],
    }
    if len(raw) > excerpt_limit:
        diagnostic["response_tail"] = raw[-min(500, excerpt_limit):]
        diagnostic["response_excerpt_truncated"] = True
    if response_diagnostic:
        diagnostic["provider_response"] = dict(response_diagnostic)

    try:
        json.loads(raw)
    except json.JSONDecodeError as exc:
        diagnostic["json_decode_error"] = {
            "message": exc.msg,
            "line": exc.lineno,
            "column": exc.colno,
            "position": exc.pos,
        }
    except TypeError as exc:
        diagnostic["json_decode_error"] = {"message": str(exc)}

    return diagnostic


def _extract_status_code(exc: Exception) -> int | None:
    direct_status = getattr(exc, "status_code", None)
    if isinstance(direct_status, int):
        return direct_status

    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if isinstance(response_status, int):
        return response_status

    return None


def _is_vertex_rate_limited(exc: Exception) -> bool:
    status_code = _extract_status_code(exc)
    if status_code == 429:
        return True

    message = str(exc).upper()
    return "RESOURCE_EXHAUSTED" in message or "RATE LIMIT" in message


def _is_overloaded(exc: Exception) -> bool:
    # 503 UNAVAILABLE ("experiencing high demand") is temporary; the free Gemini API returns it often.
    return _extract_status_code(exc) == 503 or "UNAVAILABLE" in str(exc).upper()


def _is_retryable(exc: Exception) -> bool:
    return (_is_vertex_rate_limited(exc) or _is_overloaded(exc)) and not _is_daily_quota_exhausted(exc)


def _is_daily_quota_exhausted(exc: Exception) -> bool:
    # Free-tier quota ids end in PerDay (GenerateRequestsPerDayPerProjectPerModel-FreeTier); retrying cannot help.
    return _is_vertex_rate_limited(exc) and "PERDAY" in str(exc).upper()


def _is_vertex_cache_error(exc: Exception) -> bool:
    message = str(exc).lower()
    if "cachedcontent" in message.replace("_", "").replace(" ", ""):
        return True
    return "cached content" in message or _extract_status_code(exc) == 404


def _build_vertex_error_message(exc: Exception, model_name: str, provider: str = "vertex") -> str:
    # Assemble the Vertex/Gemini API error message so callers can work from one normalized shape.
    message = str(exc).strip()
    status_code = _extract_status_code(exc)
    normalized_message = message.lower()
    service = PROVIDER_DISPLAY_NAMES[provider]

    if _is_daily_quota_exhausted(exc):
        return (
            f"{service} daily quota exhausted for model '{model_name}'; it resets at midnight Pacific time. "
            f"Last error: {message}"
        )

    if _is_overloaded(exc):
        return (
            f"{service} is overloaded for model '{model_name}' after {get_settings().vertex.max_attempts} attempts. "
            f"Last error: {message}"
        )

    if _is_vertex_rate_limited(exc):
        return (
            f"{service} rate limit hit for model '{model_name}' after {get_settings().vertex.max_attempts} attempts. "
            f"Last error: {message or 'RESOURCE_EXHAUSTED'}"
        )

    if (
        "access_token_scope_insufficient" in normalized_message
        or "insufficient authentication scopes" in normalized_message
    ):
        return (
            "Could not complete the Vertex AI request because the runtime credentials were found, "
            "but they do not include the required Cloud Platform scope for Vertex AI. "
            "If you are using local ADC, refresh them with "
            "`gcloud auth application-default login --scopes=https://www.googleapis.com/auth/cloud-platform`. "
            "If you are running on Google Cloud, ensure the attached service account has Vertex AI access "
            "and the workload/VM allows Cloud Platform scope."
        )

    if "default credentials" in normalized_message:
        return (
            "Could not complete the Vertex AI request because Application Default Credentials "
            "were not available inside the runtime."
        )

    if status_code is not None:
        return (
            f"Could not complete the {service} request for model '{model_name}' "
            f"(HTTP {status_code}). {message}"
        )

    if message:
        return (
            f"Could not complete the {service} request for model '{model_name}'. {message}"
        )

    return (
        f"Could not complete the Vertex AI request for model '{model_name}'. "
        "Verify Application Default Credentials are available and the model name is valid."
    )


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


def _ask_vertex_model(
    client: Client,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    response_schema: dict[str, Any] | None,
    response_diagnostic: dict[str, Any] | None,
    thinking_level: str | None = None,
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
    # Gemini 2.5 keeps its default thinking budget; it does not accept a thinking level.
    level = thinking_level_for(resolved_model, thinking_level or vertex_settings.thinking_level)
    if level and uses_thinking_levels(resolved_model):
        config_kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_level=level.upper())
    if response_schema is not None:
        config_kwargs["response_mime_type"] = "application/json"
        # The Gemini API's response_schema rejects additionalProperties, which the stage schemas use;
        # its JSON Schema field accepts them. Vertex keeps the field production was validated with.
        schema_field = "response_json_schema" if client.provider == "gemini" else "response_schema"
        config_kwargs[schema_field] = response_schema

    # A cached system instruction is baked into the cache, so it must not also be sent
    # inline -- the provider rejects requests that set both.
    cache_name = _cached_system_prompt_name(client, resolved_model, system_prompt)
    if cache_name is not None:
        config_kwargs["cached_content"] = cache_name
    else:
        config_kwargs["system_instruction"] = system_prompt

    retry_delay_seconds = get_settings().vertex.retry_initial_delay_seconds

    for attempt in range(1, get_settings().vertex.max_attempts + 1):
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

            if _is_retryable(exc) and attempt < get_settings().vertex.max_attempts:
                LOGGER.warning(
                    "%s %s for model '%s' (attempt %s/%s). "
                    "Sleeping %.1fs before retry.",
                    PROVIDER_DISPLAY_NAMES[client.provider],
                    "is overloaded" if _is_overloaded(exc) else "rate limit hit",
                    resolved_model,
                    attempt,
                    get_settings().vertex.max_attempts,
                    retry_delay_seconds,
                )
                time.sleep(retry_delay_seconds)
                retry_delay_seconds = min(
                    get_settings().vertex.retry_max_delay_seconds,
                    retry_delay_seconds * 2,
                )
                continue

            error_type = ModelQuotaExhausted if _is_vertex_rate_limited(exc) or _is_overloaded(exc) else RuntimeError
            raise error_type(
                _build_vertex_error_message(exc, resolved_model, client.provider)
            ) from exc

    content = _extract_response_text(response)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Model returned empty content: {response}")

    return content


def _ask_openai_model(
    client: Client,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    response_schema: dict[str, Any] | None,
    response_diagnostic: dict[str, Any] | None,
) -> str:
    resolved_model = _resolve_model_name(client.provider, model)
    request_kwargs: dict[str, Any] = {
        "model": resolved_model,
        "instructions": system_prompt,
        "input": user_prompt,
        "temperature": temperature,
    }
    if response_schema is not None:
        request_kwargs["text"] = {
            "format": {
                "type": "json_schema",
                "name": "structured_response",
                "strict": True,
                "schema": response_schema,
            }
        }

    try:
        response = client.raw_client.responses.create(**request_kwargs)
    except Exception as exc:
        raise RuntimeError(
            f"Could not complete the OpenAI request for model '{resolved_model}'. {exc}"
        ) from exc

    content = getattr(response, "output_text", None)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Model returned empty content: {response}")

    return content


def _ask_chat_completions_model(
    client: Client,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    response_schema: dict[str, Any] | None,
    response_diagnostic: dict[str, Any] | None = None,
) -> str:
    # llama-server and hosted OpenAI-compatible APIs share Chat Completions; llama-server compiles
    # a JSON schema into a grammar.
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
    }
    if response_schema is not None:
        request_kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "structured_response", "schema": response_schema},
        }

    is_llamacpp = client.provider == "llamacpp"
    try:
        response = client.raw_client.chat.completions.create(**request_kwargs)
    except Exception as exc:
        if _extract_status_code(exc) == 429:
            raise ModelQuotaExhausted(f"{client.label} rate or quota limit hit for model '{model}'. {exc}") from exc
        if is_llamacpp:
            raise RuntimeError(
                f"Could not complete the llama.cpp request. Verify llama-server is running at {client.label}. {exc}"
            ) from exc
        raise RuntimeError(f"Could not complete the request to {client.label} for model '{model}'. {exc}") from exc

    reported_model = getattr(response, "model", None)
    if response_diagnostic is not None and isinstance(reported_model, str) and reported_model:
        response_diagnostic.update({"provider": client.provider, "model_version": reported_model})

    choice = response.choices[0] if response.choices else None
    content = choice.message.content if choice is not None else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Model returned empty content: {response}")
    if choice.finish_reason == "length":
        if is_llamacpp:
            raise RuntimeError("llama.cpp truncated the response; raise llama-server's context size (-c).")
        raise RuntimeError(f"{client.label} truncated the response from model '{model}'.")

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
    response_diagnostic: dict[str, Any] | None = None,
    thinking_level: str | None = None,
) -> str:
    # Route model calls through the supported provider path and normalize the response and error handling for callers.
    # thinking_level applies to Gemini 3+ on Vertex and the Gemini API only; blank uses VERTEX_THINKING_LEVEL.
    if client.provider in GOOGLE_GENAI_PROVIDERS:
        return _ask_vertex_model(
            client,
            model,
            system_prompt,
            user_prompt,
            temperature=temperature,
            response_schema=response_schema,
            response_diagnostic=response_diagnostic,
            thinking_level=thinking_level,
        )

    if client.provider == "openai":
        return _ask_openai_model(
            client,
            model,
            system_prompt,
            user_prompt,
            temperature=temperature,
            response_schema=response_schema,
            response_diagnostic=response_diagnostic,
        )

    if client.provider in {"llamacpp", "openai_compatible"}:
        return _ask_chat_completions_model(
            client,
            model,
            system_prompt,
            user_prompt,
            temperature=temperature,
            response_schema=response_schema,
            response_diagnostic=response_diagnostic,
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


_classifier_fallback_clients: dict[str, Client] = {}


def _get_classifier_fallback_client(provider: str) -> Client:
    if provider not in _classifier_fallback_clients:
        _classifier_fallback_clients[provider] = get_model_client(provider=provider)
    return _classifier_fallback_clients[provider]


def _ask_for_answer(client: Client, model: str, system_prompt: str, user_prompt: str, **request) -> ModelAnswer:
    diagnostic: dict[str, Any] = {}
    text = ask_llm_model(client, model, system_prompt, user_prompt, response_diagnostic=diagnostic, **request)
    return ModelAnswer(text=text, provider=client.provider, model=_resolve_model_name(client.provider, model),
                       model_version=diagnostic.get("model_version"))


def ask_classifier_model(
    client: Client,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0,
    host_label: str | None = None,
    response_schema: dict[str, Any] | None = None,
    thinking_level: str | None = None,
) -> ModelAnswer:
    """Ask a news classification stage's model, then each CLASSIFIER_FALLBACKS model in turn while quota runs out.

    Free tiers count requests per model, so a second model of the same provider is a separate allowance.
    The answer names the model that produced it, so stored classifications keep their real source.
    """
    request = {"temperature": temperature, "response_schema": response_schema, "thinking_level": thinking_level}
    try:
        return _ask_for_answer(client, model, system_prompt, user_prompt, host_label=host_label, **request)
    except ModelQuotaExhausted as exc:
        refusal = exc
    for fallback in get_settings().classifier_fallback.chain:
        LOGGER.warning("%s Classifying with the %s fallback model '%s'.", refusal, fallback.provider, fallback.model)
        try:
            answer = _ask_for_answer(_get_classifier_fallback_client(fallback.provider), fallback.model,
                                     system_prompt, user_prompt, **request)
        except ModelQuotaExhausted as exc:
            refusal = exc
            continue
        return replace(answer, fallback=True)
    raise refusal


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
