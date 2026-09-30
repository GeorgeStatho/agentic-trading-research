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

from services.runtime_paths import DATA_DIR, LOGS_DIR
from services.settings import load_process_settings

# Entrypoint only: importing this module (tests) must not load .env.
if __name__ == "__main__":
    load_process_settings()

from agent_pipeline.main import run_news_collection_pipeline
from db_helpers import (
    ensure_all_sector_market_data,
    get_all_companies,
    get_all_industries,
    get_all_sectors,
    initialize_market_database,
    initialize_news_database,
)
from services.startup_readiness import StartupReadinessChecker
from services.telemetry import send_startup_ping_async


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
    # Orchestrate the cold start sanity check flow and return the next useful result for the caller.
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


def _run_startup_readiness_check() -> dict[str, Any]:
    checker = StartupReadinessChecker(LOGGER)
    readiness_result = checker.run(
        runtime_name="news_collector",
        output_paths=(SETTINGS.output_path,),
        require_llm=True,
        require_alpaca=False,
    )
    LOGGER.info("Startup readiness check passed: %s", readiness_result)
    return readiness_result


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
    from services.market_regime import build_market_regime_snapshot
    try:
        regime = build_market_regime_snapshot()
    except Exception as exc:
        LOGGER.exception("Scheduled regime input refresh failed: %s", exc)
        regime = {"error": str(exc)}
    result = run_news_collection_pipeline()
    result["market_regime_refresh"] = regime
    from services.candidate_lifecycle import observe
    from services.catalyst_response_collector import collect_catalyst_responses
    result['catalyst_response_refresh'] = observe(collect_catalyst_responses)
    from services.company_evidence_service import collect_company_evidence
    result['company_evidence_refresh'] = observe(collect_company_evidence)
    from services.event_discovery import discover_company_events
    result['event_discovery'] = observe(discover_company_events)
    JsonFileWriter.write(SETTINGS.output_path, result)
    LOGGER.info("Saved news collection output to %s", SETTINGS.output_path)
    return result


def main_loop() -> None:
    # Handle the main loop flow in one place so callers can rely on a single, well-defined result.
    LOGGER.info(
        "Starting news collector loop with interval=%s seconds",
        SETTINGS.interval_seconds,
    )

    _run_startup_readiness_check()
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
    send_startup_ping_async("news_collector", logger=LOGGER)
    main_loop()
    print(f"News collector log written to {log_path}")
