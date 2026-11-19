from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from services.core.env_parsing import env_flag
from services.core.runtime_paths import ROOT_DIR


TELEMETRY_VERSION = 1
DEFAULT_PROJECT_NAME = "Stock-trading-experiment"
DEFAULT_ENDPOINT = "https://dashboard.huvle.org/api/telemetry/startup"
DEFAULT_INSTALLATION_ID_PATH = ROOT_DIR / "shared" / "telemetry_installation.json"


@dataclass(frozen=True)
class TelemetrySettings:
    enabled: bool
    endpoint: str
    installation_id_path: Path
    timeout_seconds: float
    project_name: str
    release: str
    auth_token: str

    @property
    def is_configured(self) -> bool:
        return self.enabled and bool(self.endpoint)

    @classmethod
    def from_env(cls) -> TelemetrySettings:
        timeout_raw = str(os.getenv("TELEMETRY_TIMEOUT_SECONDS", "2.0")).strip()
        try:
            timeout_seconds = max(0.1, float(timeout_raw))
        except ValueError:
            timeout_seconds = 2.0

        return cls(
            enabled=env_flag("TELEMETRY_ENABLED", True),
            endpoint=str(os.getenv("TELEMETRY_ENDPOINT", "")).strip() or DEFAULT_ENDPOINT,
            installation_id_path=Path(
                os.getenv(
                    "TELEMETRY_INSTALLATION_ID_PATH",
                    str(DEFAULT_INSTALLATION_ID_PATH),
                )
            ),
            timeout_seconds=timeout_seconds,
            project_name=str(os.getenv("TELEMETRY_PROJECT_NAME", DEFAULT_PROJECT_NAME)).strip()
            or DEFAULT_PROJECT_NAME,
            release=str(os.getenv("TELEMETRY_RELEASE", "")).strip(),
            auth_token=str(os.getenv("TELEMETRY_AUTH_TOKEN", "")).strip(),
        )


def load_or_create_installation_metadata(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        payload = None

    if isinstance(payload, dict):
        install_id = str(payload.get("install_id") or "").strip()
        created_at = str(payload.get("created_at") or "").strip()
        if install_id and created_at:
            return {
                "install_id": install_id,
                "created_at": created_at,
            }

    metadata = {
        "install_id": str(uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def build_startup_payload(
    *,
    runtime_name: str,
    settings: TelemetrySettings,
    installation_metadata: dict[str, str],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "event": "startup",
        "telemetry_version": TELEMETRY_VERSION,
        "project": settings.project_name,
        "runtime": runtime_name,
        "install_id": str(installation_metadata.get("install_id") or "").strip(),
        "install_created_at": str(installation_metadata.get("created_at") or "").strip(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if settings.release:
        payload["release"] = settings.release
    return payload


def send_startup_ping(
    runtime_name: str,
    *,
    settings: TelemetrySettings | None = None,
    logger: logging.Logger | None = None,
) -> bool:
    settings = settings or TelemetrySettings.from_env()
    if not settings.enabled:
        return False
    if not settings.endpoint:
        if logger is not None:
            logger.warning("Telemetry is enabled but TELEMETRY_ENDPOINT is not configured.")
        return False

    installation_metadata = load_or_create_installation_metadata(settings.installation_id_path)
    payload = build_startup_payload(
        runtime_name=runtime_name,
        settings=settings,
        installation_metadata=installation_metadata,
    )
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "stock-trading-experiment-telemetry/1",
    }
    if settings.auth_token:
        headers["Authorization"] = f"Bearer {settings.auth_token}"

    request = Request(
        settings.endpoint,
        data=body,
        headers=headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=settings.timeout_seconds) as response:
            response.read(0)
        if logger is not None:
            logger.info("Startup telemetry ping sent for %s.", runtime_name)
        return True
    except (HTTPError, URLError, OSError) as exc:
        if logger is not None:
            logger.warning("Startup telemetry ping failed for %s: %s", runtime_name, exc)
        return False


def send_startup_ping_async(
    runtime_name: str,
    *,
    settings: TelemetrySettings | None = None,
    logger: logging.Logger | None = None,
) -> bool:
    settings = settings or TelemetrySettings.from_env()
    if not settings.is_configured:
        return False

    Thread(
        target=send_startup_ping,
        kwargs={
            "runtime_name": runtime_name,
            "settings": settings,
            "logger": logger,
        },
        daemon=True,
        name=f"startup-telemetry-{runtime_name}",
    ).start()
    return True
