from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from services.core.runtime_paths import LOGS_DIR


LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def configure_logging(prefix: str | None = None) -> Path | None:
    """Send INFO and above to stdout, and also to a new timestamped file when given a prefix.

    Returns the log file's path, or None for console-only logging. Every entrypoint calls this
    once under ``if __name__ == "__main__":`` so all logs share one format.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    log_path = None
    if prefix:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOGS_DIR / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        handlers.insert(0, logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=handlers, force=True)
    return log_path
