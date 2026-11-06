"""Response text, per-call diagnostics and token-usage records for every provider."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from market_db_modules.llm_usage_store import record_llm_call_usage

LOGGER = logging.getLogger(__name__)



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
            "thoughts_token_count",
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


def _record_usage(
    response_diagnostic: dict[str, Any] | None,
    provider: str,
    *,
    model_version: Any = None,
    finish_reason: Any = None,
    prompt: Any = None,
    output: Any = None,
    thoughts: Any = None,
    total: Any = None,
) -> None:
    """Record token counts under the Vertex usage names, so callers read one shape for every provider.

    ``output`` includes ``thoughts`` (the OpenAI convention); candidates are what remains.
    """
    if response_diagnostic is None:
        return
    response_diagnostic.update({"provider": provider, "finish_reason": _safe_response_value(finish_reason)})
    if isinstance(model_version, str) and model_version:
        response_diagnostic["model_version"] = model_version
    thoughts = thoughts if isinstance(thoughts, int) else 0
    counts = {
        "prompt_token_count": prompt,
        "candidates_token_count": output - thoughts if isinstance(output, int) else None,
        "thoughts_token_count": thoughts if isinstance(output, int) else None,
        "total_token_count": total,
    }
    response_diagnostic.update({key: value for key, value in counts.items() if isinstance(value, int)})


def store_call_usage(model: str, response: Any, usage: Any, stage: str | None = None) -> None:
    """Record which upstream provider answered a hosted call and what it cost; never fails the call."""
    record = {
        "model": model,
        "served_model": getattr(response, "model", None),
        "upstream_provider": getattr(response, "provider", None),
        "generation_id": getattr(response, "id", None),
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "reasoning_tokens": getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None),
        "cached_tokens": getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None),
        "cost_usd": getattr(usage, "cost", None),
        "stage": stage,
    }
    LOGGER.info("%s answered %s: %s in, %s out (%s reasoning), cost $%s", record["upstream_provider"], model,
                record["prompt_tokens"], record["completion_tokens"], record["reasoning_tokens"], record["cost_usd"])
    try:
        record_llm_call_usage(record)
    except Exception:
        LOGGER.warning("Could not record usage for model '%s'.", model, exc_info=True)
