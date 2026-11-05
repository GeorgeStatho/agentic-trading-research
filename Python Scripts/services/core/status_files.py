from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class StatusReporter:
    """Persist loop state for the dashboard and external monitors.

    A daemon thread rewrites ``updated_at`` every ``heartbeat_seconds`` while the process lives, so a
    stale file means the process is gone, not that it is sleeping or inside a long cycle.
    ``state_since`` says how long the current state has lasted, which is how a hung cycle shows up.
    """

    def __init__(self, status_path: Path, *, heartbeat_seconds: float = 30.0) -> None:
        self._status_path = status_path
        self._heartbeat_seconds = heartbeat_seconds
        self._payload: dict[str, Any] | None = None
        self._lock = threading.Lock()
        self._heartbeat: threading.Thread | None = None

    def write(self, state: str, message: str, **extra: Any) -> None:
        now = datetime.now().isoformat()
        with self._lock:
            previous = self._payload or {}
            payload = {
                "state": state,
                "message": message,
                "pid": os.getpid(),
                "updated_at": now,
                "state_since": previous.get("state_since") if previous.get("state") == state else now,
                **extra,
            }
            # The error state is overwritten by the next sleep, so keep the last failure visible to monitors.
            if state == "error":
                payload["last_error"], payload["last_error_at"] = message, now
            elif previous.get("last_error_at"):
                payload["last_error"], payload["last_error_at"] = previous["last_error"], previous["last_error_at"]
            self._payload = payload
            self._flush()
            self._ensure_heartbeat()

    def _flush(self) -> None:
        self._status_path.parent.mkdir(parents=True, exist_ok=True)
        # The API reads this file concurrently; replacing it atomically avoids serving a half-written file.
        temp_path = self._status_path.with_name(f".{self._status_path.name}.tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(self._payload, handle, ensure_ascii=True, indent=2)
        os.replace(temp_path, self._status_path)

    def _ensure_heartbeat(self) -> None:
        # Started on first write, so importers that never report (tests, other entrypoints) get no thread.
        if self._heartbeat is not None or self._heartbeat_seconds <= 0:
            return
        self._heartbeat = threading.Thread(target=self._beat, name="status-heartbeat", daemon=True)
        self._heartbeat.start()

    def _beat(self) -> None:
        while True:
            time.sleep(self._heartbeat_seconds)
            with self._lock:
                self._payload["updated_at"] = datetime.now().isoformat()
                try:
                    self._flush()
                except OSError:
                    pass  # The next beat or write retries; a monitor sees staleness if it keeps failing.


class JsonFileWriter:
    """Small utility that isolates JSON file persistence."""

    @staticmethod
    def write(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)
