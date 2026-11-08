"""Provider errors: the exceptions callers catch, and how a provider failure is classified for retries."""
from __future__ import annotations

from services.core.settings import get_settings


class ModelQuotaExhausted(RuntimeError):
    """The provider kept refusing the call for rate, quota or capacity limits."""


class ModelUnavailable(RuntimeError):
    """The model is gone (404) or its provider failed server-side (5xx); another model may still answer."""


PROVIDER_DISPLAY_NAMES: dict[str, str] = {"vertex": "Vertex AI", "gemini": "Gemini API"}


def _extract_status_code(exc: Exception) -> int | None:
    direct_status = getattr(exc, "status_code", None)
    if isinstance(direct_status, int):
        return direct_status

    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if isinstance(response_status, int):
        return response_status

    # google-genai errors carry the HTTP status as `code`.
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code

    return None


def provider_status_code(exc: BaseException) -> int | None:
    """The HTTP status behind an ask_llm_model error, whose message wraps the provider's exception."""
    for error in (exc, exc.__cause__):
        if error is None:
            continue
        status = _extract_status_code(error)
        # google-genai errors carry the status as ``code``.
        code = getattr(error, "code", None)
        status = status if status is not None else code if type(code) is int else None
        if status is not None:
            return status
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


def _is_model_unavailable(exc: Exception) -> bool:
    status_code = _extract_status_code(exc)
    return status_code == 404 or (status_code is not None and 500 <= status_code < 600)


def _is_server_error(exc: Exception) -> bool:
    # Google documents 500 INTERNAL as transient: retry with backoff before treating the model as unavailable.
    return _extract_status_code(exc) == 500


def _retry_reason(exc: Exception) -> str:
    if _is_overloaded(exc):
        return "is overloaded"
    if _is_server_error(exc):
        return "returned a server error"
    return "rate limit hit"


def _is_retryable(exc: Exception) -> bool:
    return ((_is_vertex_rate_limited(exc) or _is_overloaded(exc) or _is_server_error(exc))
            and not _is_daily_quota_exhausted(exc))


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
