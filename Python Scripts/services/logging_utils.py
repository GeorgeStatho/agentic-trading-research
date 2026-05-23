from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from services.runtime_paths import LOGS_DIR


def _build_log_path(prefix: str = "front_main") -> Path:
    """Build a timestamped log file path."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return LOGS_DIR / f"{prefix}_{timestamp}.log"


def _configure_logging(log_path: Path) -> None:
    """Configure the console and file loggers for the front entrypoint."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )
