from __future__ import annotations

from dataclasses import dataclass, replace
import logging
import os
from typing import Any

from services.core.settings import get_settings
from agent_helpers.llm_errors import ModelQuotaExhausted, ModelUnavailable
from agent_helpers.llm_openai import (_build_llamacpp_client, _build_openai_client, _build_openai_compatible_client,
                                      ask_chat_completions_model, ask_openai_model)
from agent_helpers.llm_usage import _record_usage
from agent_helpers.llm_vertex import (GOOGLE_GENAI_PROVIDERS, _build_gemini_client, _build_vertex_client,
                                      _resolve_model_name, ask_vertex_model)

try:
    from ollama import Client as OllamaClient
except ImportError:  # pragma: no cover - optional provider dependency
    OllamaClient = None


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class Client:
    provider: str
    raw_client: Any
    host: str | None = None
    label: str | None = None


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


# Explicit context caching is opt-in. Implicit caching is already on by default for
# Gemini 2.5 models, so enable this only for stages whose system prompt clears the
# provider minimum (2048 tokens) and which run many calls inside one TTL window.
# Cached tokens are billed for storage per hour, and RUN_INTERVAL_SECONDS defaults to
# three hours -- a TTL longer than one cycle's strategist/manager burst is billed idle.


def _normalize_provider(provider: str | None = None) -> str:
    return str(provider or os.getenv("LLM_PROVIDER", "ollama")).strip().lower()


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
    thinking_budget: int | None = None,
    max_output_tokens: int | None = None,
    timeout: float | None = None,
    retry: bool = True,
) -> str:
    # Route model calls through the supported provider path and normalize the response and error handling for callers.
    # thinking_level applies to Gemini 3+ on Vertex and the Gemini API (blank uses VERTEX_THINKING_LEVEL),
    # and replaces OPENAI_COMPATIBLE_REASONING_EFFORT on OpenRouter.
    # thinking_budget applies only to Gemini models without thinking levels (2.5).
    # The last four default to each provider's usual behaviour. retry=False sends exactly one request
    # (no shared or SDK retries, no context-cache creation) for callers that own their retries and spend;
    # timeout bounds that request in seconds. response_diagnostic receives token usage for every provider.
    request_controls: dict[str, Any] = {"max_output_tokens": max_output_tokens, "timeout": timeout, "retry": retry}
    if client.provider in GOOGLE_GENAI_PROVIDERS:
        return ask_vertex_model(
            client,
            model,
            system_prompt,
            user_prompt,
            temperature=temperature,
            response_schema=response_schema,
            response_diagnostic=response_diagnostic,
            thinking_level=thinking_level,
            thinking_budget=thinking_budget,
            **request_controls,
        )

    if client.provider == "openai":
        return ask_openai_model(
            client,
            model,
            system_prompt,
            user_prompt,
            temperature=temperature,
            response_schema=response_schema,
            response_diagnostic=response_diagnostic,
            **request_controls,
        )

    if client.provider in {"llamacpp", "openai_compatible"}:
        return ask_chat_completions_model(
            client,
            model,
            system_prompt,
            user_prompt,
            temperature=temperature,
            response_schema=response_schema,
            response_diagnostic=response_diagnostic,
            thinking_level=thinking_level,
            **request_controls,
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
    if max_output_tokens is not None:
        chat_kwargs["options"]["num_predict"] = max_output_tokens

    # The Ollama client only takes a timeout when it is built, so a timed call gets its own.
    ollama = OllamaClient(host=client.host, timeout=timeout) if timeout is not None else client.raw_client
    try:
        response = ollama.chat(**chat_kwargs)
    except Exception as exc:
        location = host_label or client.label or "the configured Ollama host"
        raise RuntimeError(
            "Could not complete the Ollama chat request. "
            f"Verify Ollama is running at {location} and that model '{model}' is installed."
        ) from exc

    def field(name: str) -> Any:
        return response.get(name) if hasattr(response, "get") else getattr(response, name, None)

    # Ollama counts thinking inside eval_count.
    _record_usage(response_diagnostic, client.provider, model_version=field("model"),
                  finish_reason=field("done_reason"), prompt=field("prompt_eval_count"), output=field("eval_count"))
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
    A fallback that is retired (404) or failing server-side (5xx) is skipped too; only quota moves the
    primary on, so a transient primary error does not divert calls to another, possibly paid, provider.
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
        except (ModelQuotaExhausted, ModelUnavailable) as exc:
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
