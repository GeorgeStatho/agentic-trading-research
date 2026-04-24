from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import sys
import time
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None


PYTHON_SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT_DIR = PYTHON_SCRIPTS_DIR.parent
DATA_DIR = ROOT_DIR / "Data"
LOGS_DIR = DATA_DIR / "logs"
ENV_PATH = ROOT_DIR / ".env"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"

for path in (PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.append(normalized)

if load_dotenv is not None:
    load_dotenv(ENV_PATH)

from agent_pipeline.main import run_news_collection_pipeline
from db_helpers import (
    ensure_all_sector_market_data,
    get_all_companies,
    get_all_industries,
    get_all_sectors,
    initialize_market_database,
    initialize_news_database,
)


LOGGER = logging.getLogger("news_collector")


def _env_flag(name: str, default: bool) -> bool:
    value = str(os.getenv(name, str(default))).strip().lower()
    return value not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class NewsCollectorSettings:
    interval_seconds: int
    cold_start_sanity_check_enabled: bool
    output_path: Path

    @classmethod
    def from_env(cls) -> NewsCollectorSettings:
        return cls(
            interval_seconds=max(60, int(os.getenv("NEWS_COLLECTION_INTERVAL_SECONDS", str(3 * 60 * 60)))),
            cold_start_sanity_check_enabled=_env_flag("COLD_START_SANITY_CHECK", True),
            output_path=Path(
                os.getenv(
                    "NEWS_COLLECTION_OUTPUT_PATH",
                    str(DATA_DIR / "agent_pipeline_output.json"),
                )
            ),
        )


class JsonFileWriter:
    @staticmethod
    def write(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)


def _run_cold_start_sanity_check() -> dict[str, Any]:
    initialize_market_database()
    initialize_news_database()

    sectors = get_all_sectors()
    if not sectors:
        raise RuntimeError(
            "Cold-start sanity check failed: no sectors were seeded. "
            "Verify Data/sector_etfs.json exists and is non-empty."
        )

    industries = get_all_industries()
    companies = get_all_companies()
    needs_full_market_bootstrap = not industries or not companies

    result: dict[str, Any] = {
        "sectors_seeded": len(sectors),
        "industries_present": len(industries),
        "companies_present": len(companies),
        "performed_full_market_bootstrap": needs_full_market_bootstrap,
    }

    if not needs_full_market_bootstrap:
        return result

    LOGGER.info(
        "Cold-start sanity check detected an empty market DB. Bootstrapping all %s sectors from yfinance.",
        len(sectors),
    )
    hydrated_sectors = ensure_all_sector_market_data()
    if not hydrated_sectors:
        raise RuntimeError(
            "Cold-start sanity check failed: could not hydrate sectors from yfinance."
        )

    refreshed_industries = get_all_industries()
    refreshed_companies = get_all_companies()
    result["industries_present"] = len(refreshed_industries)
    result["companies_present"] = len(refreshed_companies)
    result["hydrated_sector_count"] = len(hydrated_sectors)

    if not refreshed_industries:
        raise RuntimeError(
            "Cold-start sanity check failed: sectors seeded but no industries were loaded from yfinance."
        )
    if not refreshed_companies:
        raise RuntimeError(
            "Cold-start sanity check failed: industries loaded but no companies were loaded from yfinance."
        )

    return result


def _build_log_path(prefix: str = "news_collector") -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return LOGS_DIR / f"{prefix}_{timestamp}.log"


def _configure_logging(log_path: Path) -> None:
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


SETTINGS = NewsCollectorSettings.from_env()


def run_news_collection_cycle() -> dict[str, Any]:
    LOGGER.info("Starting news collection pipeline run")
    result = run_news_collection_pipeline()
    JsonFileWriter.write(SETTINGS.output_path, result)
    LOGGER.info("Saved news collection output to %s", SETTINGS.output_path)
    return result


def main_loop() -> None:
    LOGGER.info(
        "Starting news collector loop with interval=%s seconds",
        SETTINGS.interval_seconds,
    )

    if SETTINGS.cold_start_sanity_check_enabled:
        sanity_result = _run_cold_start_sanity_check()
        LOGGER.info("Cold-start sanity check passed: %s", sanity_result)

    next_run_at = datetime.now()

    while True:
        current_time = datetime.now()
        if current_time >= next_run_at:
            try:
                result = run_news_collection_cycle()
                LOGGER.info(
                    "News collection cycle finished with %s sectors and pipeline_mode=%s",
                    len(result.get("sectors", [])),
                    result.get("pipeline_mode"),
                )
            except Exception as exc:
                LOGGER.exception("News collection cycle failed: %s", exc)
            finally:
                next_run_at = datetime.now() + timedelta(seconds=SETTINGS.interval_seconds)

        sleep_seconds = max(1.0, (next_run_at - datetime.now()).total_seconds())
        LOGGER.info("News collector sleeping %.1f seconds until next refresh.", sleep_seconds)
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    log_path = _build_log_path()
    _configure_logging(log_path)

    LOGGER.info("News collector log started at %s", log_path)
    main_loop()
    print(f"News collector log written to {log_path}")
