from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import sys
import time
from typing import Any

from services.core.runtime_paths import DATA_DIR, LOGS_DIR
from services.core.settings import load_process_settings

# Entrypoint only: importing this module (tests) must not load .env.
if __name__ == "__main__":
    load_process_settings()

from agent_pipeline.main import run_news_collection_pipeline
from market_data.hydration import ensure_all_sector_market_data
from market_db_modules.queries import get_all_companies, get_all_industries, get_all_sectors
from market_db_modules.bootstrap import initialize_database as initialize_market_database
from news_db_modules.bootstrap import initialize_database as initialize_news_database
from services.core.env_parsing import env_flag, env_positive_int
from services.core.status_files import JsonFileWriter, StatusReporter
from services.runtime.startup_readiness import StartupReadinessChecker
from services.runtime.telemetry import send_startup_ping_async


LOGGER = logging.getLogger("news_collector")


@dataclass(frozen=True)
class NewsCollectorSettings:
    interval_seconds: int
    cold_start_sanity_check_enabled: bool
    output_path: Path
    status_path: Path

    @classmethod
    def from_env(cls) -> NewsCollectorSettings:
        return cls(
            interval_seconds=max(60, env_positive_int("NEWS_COLLECTION_INTERVAL_SECONDS", 3 * 60 * 60)),
            cold_start_sanity_check_enabled=env_flag("COLD_START_SANITY_CHECK", True),
            output_path=Path(
                os.getenv(
                    "NEWS_COLLECTION_OUTPUT_PATH",
                    str(DATA_DIR / "agent_pipeline_output.json"),
                )
            ),
            status_path=Path(os.getenv("NEWS_COLLECTOR_STATUS_PATH", str(DATA_DIR / "news_collector_status.json"))),
        )


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
STATUS_REPORTER = StatusReporter(SETTINGS.status_path)


def run_news_collection_cycle() -> dict[str, Any]:
    LOGGER.info("Starting news collection pipeline run")
    from services.gates.market_regime import build_market_regime_snapshot
    try:
        regime = build_market_regime_snapshot()
    except Exception as exc:
        LOGGER.exception("Scheduled regime input refresh failed: %s", exc)
        regime = {"error": str(exc)}
    result = run_news_collection_pipeline()
    result["market_regime_refresh"] = regime
    from services.runtime.candidate_lifecycle import observe
    from services.discovery.catalyst_response_collector import collect_catalyst_responses
    from services.discovery.catalyst_classification import classify_pending_catalysts
    from services.core.settings import get_settings
    # Before registration, so new labels are priced in the same cycle. Off until the backfill is reviewed.
    if get_settings().catalyst_classifier.enabled:
        result['catalyst_classification'] = observe(classify_pending_catalysts)
    result['catalyst_response_refresh'] = observe(collect_catalyst_responses)
    from services.evaluation.decision_attribution_runner import attribute_decisions
    result['decision_attribution'] = observe(attribute_decisions)
    from services.evaluation.forward_evaluation import run_forward_evaluation_cycle
    result['forward_evaluation'] = observe(run_forward_evaluation_cycle)
    from services.discovery.market_events import collect_market_events
    # Before evidence collection, so a newly interesting filer is collected in the same cycle.
    result['market_events'] = observe(collect_market_events)
    from services.discovery.company_evidence_service import collect_company_evidence
    result['company_evidence_refresh'] = observe(collect_company_evidence)
    from services.discovery.event_discovery import discover_company_events
    result['event_discovery'] = observe(discover_company_events)
    from services.discovery.discovery_queue import run_discovery_research_cycle
    result['discovery_research'] = observe(run_discovery_research_cycle)
    JsonFileWriter.write(SETTINGS.output_path, result)
    LOGGER.info("Saved news collection output to %s", SETTINGS.output_path)
    return result


def main_loop() -> None:
    # Handle the main loop flow in one place so callers can rely on a single, well-defined result.
    LOGGER.info(
        "Starting news collector loop with interval=%s seconds",
        SETTINGS.interval_seconds,
    )

    STATUS_REPORTER.write("starting", "Running startup checks")
    try:
        _run_startup_readiness_check()
        if SETTINGS.cold_start_sanity_check_enabled:
            sanity_result = _run_cold_start_sanity_check()
            LOGGER.info("Cold-start sanity check passed: %s", sanity_result)
    except Exception as exc:
        STATUS_REPORTER.write("error", f"Startup check failed: {exc}")
        raise

    next_run_at = datetime.now()

    while True:
        current_time = datetime.now()
        if current_time >= next_run_at:
            STATUS_REPORTER.write("running", "Collecting news")
            try:
                result = run_news_collection_cycle()
                LOGGER.info(
                    "News collection cycle finished with %s sectors and pipeline_mode=%s",
                    len(result.get("sectors", [])),
                    result.get("pipeline_mode"),
                )
            except Exception as exc:
                LOGGER.exception("News collection cycle failed: %s", exc)
                STATUS_REPORTER.write("error", f"News collection cycle failed: {exc}")
            finally:
                next_run_at = datetime.now() + timedelta(seconds=SETTINGS.interval_seconds)

        sleep_seconds = max(1.0, (next_run_at - datetime.now()).total_seconds())
        LOGGER.info("News collector sleeping %.1f seconds until next refresh.", sleep_seconds)
        STATUS_REPORTER.write("paused", "Sleeping until next refresh", sleep_seconds=sleep_seconds,
                              next_run_at=next_run_at.isoformat())
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    log_path = _build_log_path()
    _configure_logging(log_path)

    LOGGER.info("News collector log started at %s", log_path)
    send_startup_ping_async("news_collector", logger=LOGGER)
    main_loop()
    print(f"News collector log written to {log_path}")
