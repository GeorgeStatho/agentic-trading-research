from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
import sys
from typing import Any


AGENT_CALLERS_DIR = Path(__file__).resolve().parent
PYTHON_SCRIPTS_DIR = AGENT_CALLERS_DIR.parent
ROOT_DIR = PYTHON_SCRIPTS_DIR.parent
DATA_DIR = ROOT_DIR / "Data"
LOGS_DIR = DATA_DIR / "logs"

for path in (AGENT_CALLERS_DIR, PYTHON_SCRIPTS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.append(normalized)

from _paths import add_agent_caller_paths


add_agent_caller_paths()

from agent_helpers.deterministic_option_selector import apply_deterministic_option_selection
from agent_pipeline.main import run_agent_pipeline
from agent_stages.manager import decide_company_option_position
from agent_stages.strategist import decide_company_purchase


LOGGER = logging.getLogger("agent_runner")


def _build_log_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return LOGS_DIR / f"agent_runner_{timestamp}.log"


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


def _dedupe_company_symbols(pipeline_result: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    symbols: list[str] = []

    for sector in pipeline_result.get("sectors", []):
        for industry in sector.get("industries", []):
            for company in industry.get("selected_companies", []):
                symbol = str(company.get("symbol") or "").strip().upper()
                if not symbol or symbol in seen:
                    continue
                seen.add(symbol)
                symbols.append(symbol)

    return symbols


def _normalize_company_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized_symbols: list[str] = []

    for symbol in symbols:
        normalized = str(symbol or "").strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_symbols.append(normalized)

    return normalized_symbols


def _build_selected_option_output(
    *,
    ran_at: str,
    manager_results: list[dict[str, Any]],
) -> dict[str, Any]:
    companies: list[dict[str, Any]] = []

    for manager_result in manager_results:
        company = manager_result.get("company", {})
        recommendation = manager_result.get("recommendation", {})

        companies.append(
            {
                "company_id": company.get("company_id"),
                "symbol": company.get("symbol"),
                "name": company.get("name"),
                "sector_key": company.get("sector_key"),
                "industry_key": company.get("industry_key"),
                "decision": recommendation.get("decision"),
                "confidence": recommendation.get("confidence"),
                "selected_option_id": recommendation.get("selected_option_id"),
                "selected_expiration_date": recommendation.get("selected_expiration_date"),
                "selected_strike_price": recommendation.get("selected_strike_price"),
                "selected_option_source": recommendation.get("selected_option_source"),
                "selection_debug": recommendation.get("selection_debug", {}),
                "selected_option": manager_result.get("selected_option"),
                "reason": recommendation.get("reason"),
            }
        )

    return {
        "ran_at": ran_at,
        "selected_option_count": sum(1 for company in companies if company.get("selected_option_id") is not None),
        "companies": companies,
    }


def _run_strategist_and_manager(company_symbols: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    strategist_results: list[dict[str, Any]] = []
    manager_results: list[dict[str, Any]] = []

    LOGGER.info("Running strategist and manager for %s selected companies", len(company_symbols))

    for symbol in company_symbols:
        LOGGER.info("Running strategist for %s", symbol)
        strategist_result = decide_company_purchase(symbol)
        strategist_results.append(strategist_result)
        LOGGER.info(
            "Finished strategist for %s with decision=%s confidence=%s",
            symbol,
            strategist_result.get("recommendation", {}).get("decision"),
            strategist_result.get("recommendation", {}).get("confidence"),
        )

        LOGGER.info("Running manager for %s", symbol)
        manager_result = decide_company_option_position(symbol)
        manager_result = apply_deterministic_option_selection(manager_result)
        manager_results.append(manager_result)
        LOGGER.info(
            "Finished manager for %s with decision=%s confidence=%s selected_option_id=%s expiration=%s strike=%s",
            symbol,
            manager_result.get("recommendation", {}).get("decision"),
            manager_result.get("recommendation", {}).get("confidence"),
            manager_result.get("recommendation", {}).get("selected_option_id"),
            manager_result.get("recommendation", {}).get("selected_expiration_date"),
            manager_result.get("recommendation", {}).get("selected_strike_price"),
        )

    return strategist_results, manager_results


def run_full_agent_stack() -> dict[str, Any]:
    LOGGER.info("Starting agent pipeline stage")
    pipeline_result = run_agent_pipeline()
    LOGGER.info("Finished agent pipeline stage")

    company_symbols = _dedupe_company_symbols(pipeline_result)
    strategist_results, manager_results = _run_strategist_and_manager(company_symbols)
    ran_at = datetime.now().isoformat()

    return {
        "ran_at": ran_at,
        "company_symbols": company_symbols,
        "pipeline": pipeline_result,
        "strategist": strategist_results,
        "manager": manager_results,
        "selected_options": _build_selected_option_output(ran_at=ran_at, manager_results=manager_results),
    }


def run_strategist_manager_only(company_symbols: list[str]) -> dict[str, Any]:
    normalized_symbols = _normalize_company_symbols(company_symbols)
    strategist_results, manager_results = _run_strategist_and_manager(normalized_symbols)
    ran_at = datetime.now().isoformat()

    return {
        "ran_at": ran_at,
        "company_symbols": normalized_symbols,
        "pipeline": None,
        "strategist": strategist_results,
        "manager": manager_results,
        "selected_options": _build_selected_option_output(ran_at=ran_at, manager_results=manager_results),
    }


if __name__ == "__main__":
    log_path = _build_log_path()
    _configure_logging(log_path)

    LOGGER.info("Agent runner log started at %s", log_path)
    result = run_full_agent_stack()

    output_path = DATA_DIR / "agent_runner_output.json"
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=True, indent=2)

    selected_options_output_path = DATA_DIR / "selected_options_output.json"
    with selected_options_output_path.open("w", encoding="utf-8") as handle:
        json.dump(result.get("selected_options", {}), handle, ensure_ascii=True, indent=2)

    LOGGER.info("Saved agent runner output to %s", output_path)
    LOGGER.info("Saved selected options output to %s", selected_options_output_path)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    print(f"Saved agent runner output to {output_path}")
    print(f"Saved selected options output to {selected_options_output_path}")
    print(f"Agent runner log written to {log_path}")
