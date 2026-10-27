from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


class StatusReporter:
    """Persist loop state for the dashboard and external monitors."""

    def __init__(self, status_path: Path) -> None:
        self._status_path = status_path

    def write(self, state: str, message: str, **extra: Any) -> None:
        payload = {
            "state": state,
            "message": message,
            "pid": os.getpid(),
            "updated_at": datetime.now().isoformat(),
            **extra,
        }
        self._status_path.parent.mkdir(parents=True, exist_ok=True)
        with self._status_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)


class JsonFileWriter:
    """Small utility that isolates JSON file persistence."""

    @staticmethod
    def write(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)
