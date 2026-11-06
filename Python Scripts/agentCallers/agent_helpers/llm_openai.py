"""OpenAI, OpenAI-compatible (OpenRouter, Mistral, ...) and llama.cpp clients and calls."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from services.core.settings import get_settings
from agent_helpers.llm_errors import ModelQuotaExhausted, ModelUnavailable, _extract_status_code, _is_model_unavailable
from agent_helpers.llm_usage import _record_usage, store_call_usage
from agent_helpers.llm_vertex import _resolve_model_name

if TYPE_CHECKING:
    from agent_helpers.llm_client import Client

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional provider dependency
    OpenAI = None


LLAMACPP_TIMEOUT_SECONDS = 1800


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


def _strict_compatible(schema: Any) -> bool:
    # OpenAI strict mode needs every object closed and every property required; it rejects other schemas.
    if isinstance(schema, list):
        return all(_strict_compatible(item) for item in schema)
    if not isinstance(schema, dict):
        return True
    properties = schema.get("properties")
    if isinstance(properties, dict) and (schema.get("additionalProperties") is not False
                                         or set(schema.get("required", ())) != set(properties)):
        return False
    return all(_strict_compatible(value) for value in schema.values())


def _request_client(client: Client, timeout: float | None, retry: bool) -> Any:
    # The OpenAI SDK retries by default; with_options copies the client for this request only.
    if timeout is None and retry:
        return client.raw_client
    options: dict[str, Any] = {} if retry else {"max_retries": 0}
    if timeout is not None:
        options["timeout"] = timeout
    return client.raw_client.with_options(**options)


def ask_openai_model(
    client: Client,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    response_schema: dict[str, Any] | None,
    response_diagnostic: dict[str, Any] | None,
    max_output_tokens: int | None = None,
    timeout: float | None = None,
    retry: bool = True,
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
                "strict": _strict_compatible(response_schema),
                "schema": response_schema,
            }
        }
    if max_output_tokens is not None:
        request_kwargs["max_output_tokens"] = max_output_tokens

    try:
        response = _request_client(client, timeout, retry).responses.create(**request_kwargs)
    except Exception as exc:
        raise RuntimeError(
            f"Could not complete the OpenAI request for model '{resolved_model}'. {exc}"
        ) from exc

    usage = getattr(response, "usage", None)
    incomplete = getattr(getattr(response, "incomplete_details", None), "reason", None)
    _record_usage(response_diagnostic, client.provider, model_version=getattr(response, "model", None),
                  finish_reason=incomplete or getattr(response, "status", None),
                  prompt=getattr(usage, "input_tokens", None), output=getattr(usage, "output_tokens", None),
                  thoughts=getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", None),
                  total=getattr(usage, "total_tokens", None))
    content = getattr(response, "output_text", None)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Model returned empty content: {response}")

    return content


def _openrouter_options(hosted: Any, *, has_schema: bool = False, thinking_level: str | None = None) -> dict[str, Any]:
    # usage.include makes OpenRouter report the call's cost alongside its tokens.
    options: dict[str, Any] = {"usage": {"include": True}}
    # A stage's own thinking level (e.g. a low-thinking classifier on fallback) beats the global effort.
    effort = thinking_level or hosted.reasoning_effort
    if effort:
        options["reasoning"] = {"effort": effort}
    provider: dict[str, Any] = {}
    if hosted.provider_order:
        # Fallbacks keep a call alive when the preferred providers are down; the usage row names who answered.
        provider = {"order": list(hosted.provider_order), "allow_fallbacks": hosted.provider_fallbacks}
    if hosted.max_price is not None:
        provider["max_price"] = {"prompt": hosted.max_price[0], "completion": hosted.max_price[1]}
    if has_schema:
        # Without this, a provider lacking structured outputs may receive the call and ignore the schema.
        provider["require_parameters"] = True
    if provider:
        options["provider"] = provider
    return options


def ask_chat_completions_model(
    client: Client,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    response_schema: dict[str, Any] | None,
    response_diagnostic: dict[str, Any] | None = None,
    thinking_level: str | None = None,
    max_output_tokens: int | None = None,
    timeout: float | None = None,
    retry: bool = True,
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
    if client.provider == "openai_compatible":
        hosted = get_settings().openai_compatible
        if hosted.temperature is not None:
            request_kwargs["temperature"] = hosted.temperature
        request_kwargs["max_tokens"] = hosted.max_tokens
        if hosted.is_openrouter:
            request_kwargs["extra_body"] = _openrouter_options(hosted, has_schema=response_schema is not None,
                                                               thinking_level=thinking_level)
    if max_output_tokens is not None:
        request_kwargs["max_tokens"] = max_output_tokens

    is_llamacpp = client.provider == "llamacpp"
    try:
        response = _request_client(client, timeout, retry).chat.completions.create(**request_kwargs)
    except Exception as exc:
        if _extract_status_code(exc) == 429:
            raise ModelQuotaExhausted(f"{client.label} rate or quota limit hit for model '{model}'. {exc}") from exc
        if is_llamacpp:
            raise RuntimeError(
                f"Could not complete the llama.cpp request. Verify llama-server is running at {client.label}. {exc}"
            ) from exc
        error_type = ModelUnavailable if _is_model_unavailable(exc) else RuntimeError
        raise error_type(f"Could not complete the request to {client.label} for model '{model}'. {exc}") from exc

    choice = response.choices[0] if response.choices else None
    usage = getattr(response, "usage", None)
    _record_usage(response_diagnostic, client.provider, model_version=getattr(response, "model", None),
                  finish_reason=getattr(choice, "finish_reason", None),
                  prompt=getattr(usage, "prompt_tokens", None), output=getattr(usage, "completion_tokens", None),
                  thoughts=getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None),
                  total=getattr(usage, "total_tokens", None))
    if client.provider == "openai_compatible" and usage is not None:
        store_call_usage(model, response, usage)

    content = choice.message.content if choice is not None else None
    # Before the empty check: reasoning can use the whole budget and leave no content.
    if choice is not None and choice.finish_reason == "length":
        if is_llamacpp:
            raise RuntimeError("llama.cpp truncated the response; raise llama-server's context size (-c).")
        raise RuntimeError(f"{client.label} truncated the response from model '{model}'.")
    if not isinstance(content, str) or not content.strip():
        # A response can carry a long reasoning trace; keep the error readable.
        raise RuntimeError(f"Model returned empty content: {str(response)[:1000]}")

    return content
