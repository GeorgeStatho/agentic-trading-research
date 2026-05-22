from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
import sys
from typing import Any, Callable

if __package__ in {None, ""}:
    AGENT_CALLERS_DIR = Path(__file__).resolve().parent
    if str(AGENT_CALLERS_DIR) not in sys.path:
        sys.path.append(str(AGENT_CALLERS_DIR))

from _paths import DATA_DIR, LOGS_DIR, ROOT_DIR, bootstrap_agent_callers, load_project_env


if __name__ == "__main__":
    load_project_env()

bootstrap_agent_callers()

from agent_helpers.deterministic_option_selector import apply_deterministic_option_selection
from agent_helpers.company_opportunist import get_company_opportunist_summary
from agent_helpers.opportunist_payload import DEFAULT_MAX_ARTICLE_AGE_DAYS
from agent_pipeline.main import run_agent_pipeline, run_agent_pipeline_from_existing_data
from agent_stages.manager import MANAGER_STAGE_VERSION, decide_company_option_position
from agent_stages.strategist import decide_company_purchase
from db_helpers.market import record_manager_decision_history
from services.config import AgentPipelineSettings


LOGGER = logging.getLogger("agent_runner")
ProgressCallback = Callable[[dict[str, Any]], None]


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


def _build_company_context_by_symbol(
    pipeline_result: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}

    for sector in pipeline_result.get("sectors", []):
        sector_key = str(sector.get("sector_key") or "").strip()
        for industry in sector.get("industries", []):
            industry_payload = industry.get("industry", {})
            industry_key = str(
                industry_payload.get("industry_key")
                or industry_payload.get("key")
                or ""
            ).strip()
            industry_name = str(
                industry_payload.get("name")
                or industry_payload.get("industry_name")
                or industry_key
            ).strip()
            for company in industry.get("selected_companies", []):
                symbol = str(company.get("symbol") or "").strip().upper()
                if not symbol or symbol in contexts:
                    continue
                contexts[symbol] = {
                    "company_id": company.get("company_id"),
                    "company_name": company.get("name"),
                    "sector_key": sector_key,
                    "industry_key": industry_key,
                    "industry_name": industry_name,
                }

    return contexts


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


def _emit_progress(
    on_progress: ProgressCallback | None,
    payload: dict[str, Any],
) -> None:
    if on_progress is None:
        return
    on_progress(payload)


def _filter_company_symbols_by_high_confidence_support(
    company_symbols: list[str],
    *,
    minimum_high_confidence_articles: int,
    max_age_days: int | None,
) -> tuple[list[str], list[dict[str, Any]]]:
    normalized_symbols = _normalize_company_symbols(company_symbols)
    if minimum_high_confidence_articles <= 0:
        return normalized_symbols, []

    allowed_symbols: list[str] = []
    skipped_companies: list[dict[str, Any]] = []

    for symbol in normalized_symbols:
        try:
            summary = get_company_opportunist_summary(symbol, max_age_days=max_age_days)
        except Exception:
            LOGGER.exception(
                "Failed to load company opportunist summary for %s while enforcing the strategist/manager article gate",
                symbol,
            )
            skipped_companies.append(
                {
                    "symbol": symbol,
                    "high_confidence_article_count": 0,
                    "minimum_required_high_confidence_articles": minimum_high_confidence_articles,
                    "window_max_age_days": max_age_days,
                    "skip_reason": "summary_lookup_failed",
                }
            )
            continue

        confidence_counts = summary.get("confidence_counts", {})
        high_confidence_article_count = 0
        if isinstance(confidence_counts, dict):
            try:
                high_confidence_article_count = int(confidence_counts.get("high", 0) or 0)
            except (TypeError, ValueError):
                high_confidence_article_count = 0

        if high_confidence_article_count >= minimum_high_confidence_articles:
            allowed_symbols.append(symbol)
            continue

        company = summary.get("company", {})
        skipped_companies.append(
            {
                "symbol": symbol,
                "company_id": company.get("company_id"),
                "name": company.get("name"),
                "high_confidence_article_count": high_confidence_article_count,
                "minimum_required_high_confidence_articles": minimum_high_confidence_articles,
                "window_max_age_days": max_age_days,
                "skip_reason": "insufficient_high_confidence_articles",
            }
        )

    if skipped_companies:
        LOGGER.info(
            "Skipped %s companies before strategist/manager due to insufficient recent high-confidence company articles: %s",
            len(skipped_companies),
            ", ".join(
                f"{item.get('symbol')}({item.get('high_confidence_article_count')}/{item.get('minimum_required_high_confidence_articles')})"
                for item in skipped_companies
            ),
        )

    return allowed_symbols, skipped_companies


def _build_selected_option_output(
    *,
    ran_at: str,
    manager_results: list[dict[str, Any]],
) -> dict[str, Any]:
    # Translate manager results into the compact selected-options payload written to disk and exposed to the UI.
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
                "strategist_recommendation": manager_result.get("strategist_recommendation", {}),
                "selected_option": manager_result.get("selected_option"),
                "reason": recommendation.get("reason"),
            }
        )

    return {
        "ran_at": ran_at,
        "selected_option_count": sum(1 for company in companies if company.get("selected_option_id") is not None),
        "companies": companies,
    }


def _summarize_selector_rejection_reasons(manager_result: dict[str, Any]) -> str:
    # Aggregate selector rejection reasons so failed contract picks still produce readable diagnostics.
    recommendation = manager_result.get("recommendation", {})
    selection_debug = recommendation.get("selection_debug", {})
    nested_selector_debug = selection_debug.get("selector_debug", {})

    rejection_examples: list[dict[str, Any]] = []

    def _collect_rejection_examples(value: Any) -> None:
        if isinstance(value, dict):
            for key, nested_value in value.items():
                if key.endswith("_examples") and isinstance(nested_value, list):
                    for example in nested_value:
                        if isinstance(example, dict) and isinstance(
                            example.get("rejection_reasons"), list
                        ):
                            rejection_examples.append(example)
                else:
                    _collect_rejection_examples(nested_value)
        elif isinstance(value, list):
            for item in value:
                _collect_rejection_examples(item)

    _collect_rejection_examples(nested_selector_debug)

    if not rejection_examples:
        selection_mode = str(selection_debug.get("selection_mode") or "").strip()
        if selection_mode:
            return f"none_recorded mode={selection_mode}"
        return "none_recorded"

    reason_counts: dict[str, int] = {}
    for example in rejection_examples:
        if not isinstance(example, dict):
            continue
        reasons = example.get("rejection_reasons", [])
        if not isinstance(reasons, list):
            continue
        for reason in reasons:
            normalized_reason = str(reason or "").strip()
            if not normalized_reason:
                continue
            reason_counts[normalized_reason] = reason_counts.get(normalized_reason, 0) + 1

    if not reason_counts:
        return "none_recorded"

    top_reasons = sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))[:3]
    return ", ".join(f"{reason}({count})" for reason, count in top_reasons)


def _extract_manager_article_references(manager_result: dict[str, Any]) -> list[dict[str, Any]]:
    supporting_articles = manager_result.get("supporting_articles", {})
    if not isinstance(supporting_articles, dict):
        return []
    article_summaries = supporting_articles.get("article_summaries", [])
    if not isinstance(article_summaries, list):
        return []

    references: list[dict[str, Any]] = []
    for article in article_summaries[:5]:
        if not isinstance(article, dict):
            continue
        references.append(
            {
                "article_id": int(article.get("article_id") or 0),
                "title": str(article.get("title") or ""),
                "source": str(article.get("source") or ""),
                "published_at": str(article.get("published_at") or ""),
                "article_scope": str(article.get("article_scope") or ""),
                "evidence_layers": [
                    str(layer or "").strip()
                    for layer in article.get("evidence_layers", [])
                    if str(layer or "").strip()
                ],
            }
        )
    return references


def _record_manager_decision_history_for_result(manager_result: dict[str, Any]) -> int | None:
    if not isinstance(manager_result, dict):
        return None

    company = dict(manager_result.get("company") or {})
    recommendation = dict(manager_result.get("recommendation") or {})
    strategist_recommendation = dict(manager_result.get("strategist_recommendation") or {})
    selected_option = dict(manager_result.get("selected_option") or {})
    selected_option_symbol = str(selected_option.get("symbol") or "").strip().upper() or None
    decision_run_at = str(manager_result.get("ran_at") or datetime.now().isoformat())

    history_payload = {
        "company_id": company.get("company_id"),
        "symbol": company.get("symbol"),
        "company_name": company.get("name"),
        "decision_run_at": decision_run_at,
        "manager_stage_version": MANAGER_STAGE_VERSION,
        "strategist_decision": strategist_recommendation.get("decision"),
        "manager_decision": recommendation.get("decision"),
        "manager_confidence": recommendation.get("confidence"),
        "manager_reason": recommendation.get("reason"),
        "target_dte_bucket": recommendation.get("target_dte_bucket"),
        "selected_option_id": recommendation.get("selected_option_id"),
        "selected_option_symbol": selected_option_symbol,
        "selected_expiration_date": recommendation.get("selected_expiration_date"),
        "selected_strike_price": recommendation.get("selected_strike_price"),
        "selected_option_source": recommendation.get("selected_option_source"),
        "manager_input_json": {
            "company": company,
            "context_snapshot": manager_result.get("context_snapshot", {}),
            "market_context": manager_result.get("market_context", {}),
            "strategist_recommendation": strategist_recommendation,
            "article_references": _extract_manager_article_references(manager_result),
            "recent_manager_decision_history": manager_result.get("recent_manager_decision_history", []),
        },
        "manager_output_json": {
            "recommendation": recommendation,
            "selected_option": selected_option,
        },
    }
    return record_manager_decision_history(history_payload)


def _run_strategist_and_manager(
    company_symbols: list[str],
    *,
    company_context_by_symbol: dict[str, dict[str, Any]] | None = None,
    on_progress: ProgressCallback | None = None,
    on_manager_result: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Run the strategist and manager stages for one company and keep their outputs aligned for the final payload.
    strategist_results: list[dict[str, Any]] = []
    manager_results: list[dict[str, Any]] = []

    LOGGER.info("Running strategist and manager for %s selected companies", len(company_symbols))

    for symbol in company_symbols:
        company_context = dict((company_context_by_symbol or {}).get(symbol) or {})
        progress_base = {
            "current_symbol": symbol,
            "current_company_name": company_context.get("company_name"),
            "current_sector": company_context.get("sector_key"),
            "current_industry": (
                company_context.get("industry_name")
                or company_context.get("industry_key")
            ),
        }
        _emit_progress(
            on_progress,
            {
                "stage": "strategist",
                "message": f"Running strategist for {symbol}",
                **progress_base,
            },
        )
        LOGGER.info("Running strategist for %s", symbol)
        strategist_result = decide_company_purchase(symbol)
        strategist_results.append(strategist_result)
        LOGGER.info(
            "Finished strategist for %s with decision=%s confidence=%s",
            symbol,
            strategist_result.get("recommendation", {}).get("decision"),
            strategist_result.get("recommendation", {}).get("confidence"),
        )

        _emit_progress(
            on_progress,
            {
                "stage": "manager",
                "message": f"Running manager for {symbol}",
                **progress_base,
            },
        )
        LOGGER.info("Running manager for %s", symbol)
        manager_result = decide_company_option_position(
            symbol,
            strategist_recommendation=strategist_result.get("recommendation", {}),
        )
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
        if manager_result.get("recommendation", {}).get("selected_option_id") is None:
            LOGGER.info(
                "No selected option for %s: source=%s top_rejection_reasons=%s",
                symbol,
                manager_result.get("recommendation", {}).get("selected_option_source"),
                _summarize_selector_rejection_reasons(manager_result),
            )
        try:
            decision_history_id = _record_manager_decision_history_for_result(manager_result)
            if decision_history_id is not None:
                manager_result["manager_decision_history_id"] = decision_history_id
        except Exception:
            LOGGER.exception("Failed to record manager decision history for %s", symbol)
        if on_manager_result is not None:
            on_manager_result(manager_result)

    return strategist_results, manager_results


def run_full_agent_stack(
    *,
    on_progress: ProgressCallback | None = None,
    on_manager_result: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the full agent pipeline plus strategist and manager follow-up stages."""
    _emit_progress(
        on_progress,
        {
            "stage": "pipeline",
            "message": "Running pipeline refresh",
        },
    )
    LOGGER.info("Starting agent pipeline stage")
    pipeline_result = run_agent_pipeline(on_progress=on_progress)
    LOGGER.info("Finished agent pipeline stage")

    pipeline_settings = AgentPipelineSettings.from_env()
    company_context_by_symbol = _build_company_context_by_symbol(pipeline_result)
    company_symbols, skipped_companies = _filter_company_symbols_by_high_confidence_support(
        _dedupe_company_symbols(pipeline_result),
        minimum_high_confidence_articles=pipeline_settings.minimum_high_confidence_company_articles,
        max_age_days=DEFAULT_MAX_ARTICLE_AGE_DAYS,
    )
    strategist_results, manager_results = _run_strategist_and_manager(
        company_symbols,
        company_context_by_symbol=company_context_by_symbol,
        on_progress=on_progress,
        on_manager_result=on_manager_result,
    )
    ran_at = datetime.now().isoformat()

    return {
        "ran_at": ran_at,
        "company_symbols": company_symbols,
        "skipped_companies_before_strategist_manager": skipped_companies,
        "pipeline": pipeline_result,
        "strategist": strategist_results,
        "manager": manager_results,
        "selected_options": _build_selected_option_output(ran_at=ran_at, manager_results=manager_results),
    }


def run_full_agent_stack_from_existing_data(
    *,
    on_progress: ProgressCallback | None = None,
    on_manager_result: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run strategist and manager stages using the current persisted DB pipeline state."""
    _emit_progress(
        on_progress,
        {
            "stage": "pipeline",
            "message": "Loading pipeline targets from existing data",
        },
    )
    LOGGER.info("Starting agent pipeline stage using existing DB data")
    pipeline_result = run_agent_pipeline_from_existing_data(on_progress=on_progress)
    LOGGER.info("Finished agent pipeline stage using existing DB data")

    pipeline_settings = AgentPipelineSettings.from_env()
    company_context_by_symbol = _build_company_context_by_symbol(pipeline_result)
    company_symbols, skipped_companies = _filter_company_symbols_by_high_confidence_support(
        _dedupe_company_symbols(pipeline_result),
        minimum_high_confidence_articles=pipeline_settings.minimum_high_confidence_company_articles,
        max_age_days=DEFAULT_MAX_ARTICLE_AGE_DAYS,
    )
    strategist_results, manager_results = _run_strategist_and_manager(
        company_symbols,
        company_context_by_symbol=company_context_by_symbol,
        on_progress=on_progress,
        on_manager_result=on_manager_result,
    )
    ran_at = datetime.now().isoformat()

    return {
        "ran_at": ran_at,
        "company_symbols": company_symbols,
        "skipped_companies_before_strategist_manager": skipped_companies,
        "pipeline": pipeline_result,
        "strategist": strategist_results,
        "manager": manager_results,
        "selected_options": _build_selected_option_output(ran_at=ran_at, manager_results=manager_results),
    }


def run_strategist_manager_only(
    company_symbols: list[str],
    *,
    on_progress: ProgressCallback | None = None,
    on_manager_result: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run only the strategist and manager stages for explicit company symbols."""
    normalized_symbols = _normalize_company_symbols(company_symbols)
    strategist_results, manager_results = _run_strategist_and_manager(
        normalized_symbols,
        on_progress=on_progress,
        on_manager_result=on_manager_result,
    )
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
