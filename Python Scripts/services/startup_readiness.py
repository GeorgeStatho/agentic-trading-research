from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import time
from typing import Any, Iterable

from agent_helpers.shared import ask_llm_model, get_model_client

from services.common import env_flag, safe_float


def startup_readiness_enabled() -> bool:
    """Return whether startup readiness checks should run."""
    return env_flag("STARTUP_READINESS_CHECK", True)


def _first_configured_env(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return default


def resolve_startup_readiness_model() -> str:
    """Resolve the lightweight model used by the startup readiness probe."""
    return _first_configured_env(
        "STARTUP_READINESS_MODEL",
        "WORLD_NEWS_MODEL",
        "MACRO_NEWS_MODEL",
        "STRATEGIST_MODEL",
        "MANAGER_MODEL",
        "VERTEX_DEFAULT_MODEL",
        "RESEARCHER_MODEL",
        default="llama3.1",
    )


def resolve_startup_readiness_backend_label() -> str:
    """Resolve the backend label or Ollama host used for probe error messages."""
    return _first_configured_env(
        "STARTUP_READINESS_OLLAMA_HOST",
        "OLLAMA_HOST",
        "WORLD_NEWS_OLLAMA_HOST",
        "MACRO_NEWS_OLLAMA_HOST",
        "STRATEGIST_OLLAMA_HOST",
        "MANAGER_OLLAMA_HOST",
        "COMPANY_OPPURUNTIST_OLLAMA_HOST",
        "INDUSTRY_OPPURUNTINST_OLLAMA_HOST",
        "SECTOR_OPPURUNTIST_OLLAMA_HOST",
        default="http://127.0.0.1:11434",
    )


class StartupReadinessChecker:
    """Run one-time startup checks for live dependencies and writable outputs."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def run(
        self,
        *,
        runtime_name: str,
        output_paths: Iterable[Path] = (),
        require_llm: bool = True,
        require_alpaca: bool = False,
        trading_gateway: Any | None = None,
        alpaca_paper: bool | None = None,
    ) -> dict[str, Any]:
        if not startup_readiness_enabled():
            result = {
                "runtime": runtime_name,
                "enabled": False,
                "skipped": True,
                "checks": [],
            }
            self._logger.info(
                "Startup readiness check skipped for %s because STARTUP_READINESS_CHECK is disabled.",
                runtime_name,
            )
            return result

        started_at = datetime.now(timezone.utc)
        checks: list[dict[str, Any]] = []

        if env_flag("STARTUP_READINESS_OUTPUT_PATH_CHECK", True):
            for output_path in output_paths:
                checks.append(self._check_output_path(output_path))

        if require_llm and env_flag("STARTUP_READINESS_LLM_CHECK", True):
            checks.append(self._check_llm())

        if require_alpaca and env_flag("STARTUP_READINESS_ALPACA_CHECK", True):
            if trading_gateway is None:
                raise RuntimeError("trading_gateway is required when require_alpaca=True.")
            checks.append(
                self._check_alpaca(
                    trading_gateway=trading_gateway,
                    alpaca_paper=alpaca_paper,
                )
            )

        completed_at = datetime.now(timezone.utc)
        return {
            "runtime": runtime_name,
            "enabled": True,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": round((completed_at - started_at).total_seconds(), 3),
            "checks": checks,
        }

    def _check_output_path(self, output_path: Path) -> dict[str, Any]:
        normalized_path = Path(output_path)
        parent = normalized_path.parent
        parent.mkdir(parents=True, exist_ok=True)

        probe_path = parent / (
            f".startup-readiness-{os.getpid()}-{time.time_ns()}.tmp"
        )
        try:
            with probe_path.open("w", encoding="utf-8") as handle:
                handle.write("startup_readiness_ok\n")
        finally:
            try:
                probe_path.unlink(missing_ok=True)
            except TypeError:
                if probe_path.exists():
                    probe_path.unlink()

        return {
            "name": "output_path",
            "path": str(normalized_path),
            "parent": str(parent),
        }

    def _check_llm(self) -> dict[str, Any]:
        model_name = resolve_startup_readiness_model()
        backend_label = resolve_startup_readiness_backend_label()
        client = get_model_client(backend_label)
        response = ask_llm_model(
            client,
            model_name,
            "Reply with READY.",
            "Reply with READY.",
            temperature=0,
            host_label=backend_label,
        )
        normalized_response = str(response or "").strip()
        if "READY" not in normalized_response.upper():
            raise RuntimeError(
                "Startup readiness LLM probe returned an unexpected response. "
                f"Expected READY but received: {normalized_response[:120]}"
            )

        return {
            "name": "llm",
            "provider": client.provider,
            "model": model_name,
            "backend_label": backend_label,
            "response_preview": normalized_response[:64],
        }

    def _check_alpaca(
        self,
        *,
        trading_gateway: Any,
        alpaca_paper: bool | None,
    ) -> dict[str, Any]:
        trading_client = trading_gateway.create_client()
        account = trading_client.get_account()
        clock = trading_client.get_clock()

        return {
            "name": "alpaca",
            "paper": bool(alpaca_paper),
            "account_status": str(getattr(account, "status", "") or ""),
            "buying_power": safe_float(getattr(account, "buying_power", None)),
            "clock_is_open": bool(getattr(clock, "is_open", False)),
            "clock_timestamp": str(getattr(clock, "timestamp", "") or ""),
        }
