from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


LOG_DIR = Path(__file__).resolve().parents[3] / "Data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_SESSION_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
_LOG_FILE_PATH = LOG_DIR / f"scraper_{_SESSION_TIMESTAMP}.log"
_BASE_LOGGER_NAME = "stock_scraper"


def _configure_base_logger() -> logging.Logger:
    logger = logging.getLogger(_BASE_LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    file_handler = logging.FileHandler(_LOG_FILE_PATH, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # propagate is off, so without this scraper failures never reach stdout (docker logs).
    # INFO is per-article chatter (~200 lines a minute) and stays in the file.
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.WARNING)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


def get_scrape_logger(name: str | None = None) -> logging.Logger:
    _configure_base_logger()
    if not name:
        return logging.getLogger(_BASE_LOGGER_NAME)
    return logging.getLogger(f"{_BASE_LOGGER_NAME}.{name}")


def get_scrapy_log_settings() -> dict[str, str | bool]:
    _configure_base_logger()
    return {
        "LOG_ENABLED": True,
        "LOG_FILE": str(_LOG_FILE_PATH),
        "LOG_LEVEL": "INFO",
    }


def get_log_file_path() -> Path:
    _configure_base_logger()
    return _LOG_FILE_PATH
