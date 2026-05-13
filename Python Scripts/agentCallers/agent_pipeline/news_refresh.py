from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from agent_pipeline.ranking import (
    DEFAULT_TOP_COMPANY_COUNT,
    DEFAULT_TOP_INDUSTRY_COUNT,
    DEFAULT_TOP_SECTOR_COUNT,
    RANKING_MAX_AGE_DAYS_UNSET,
    _get_ranked_industries_for_sector,
    get_current_rankings,
    resolve_ranking_max_age_days,
)
from agent_pipeline.target_selection import (
    build_company_opportunist_summary,
    collect_ranked_companies_for_industry,
)
from agent_stages.company_opportunist import classify_company_articles
from agent_stages.industry_opportunist import classify_sector_articles_to_industries
from agent_stages.macro_news_to_sectors import classify_macro_news_to_sectors
from agent_stages.sector_opportunist import classify_sector_articles


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SUBPROCESS_IMPORT_PATHS = [
    str(PROJECT_ROOT / "Python Scripts" / "Webscraping"),
    str(PROJECT_ROOT / "Python Scripts" / "agentCallers"),
]
SCRAPE_RESULT_MARKER = "__SCRAPE_RESULT__="


def _run_scrape_subprocess(
    module_name: str,
    function_name: str,
    *args: Any,
    **kwargs: Any,
) -> int:
    runner_payload = {
        "paths": SUBPROCESS_IMPORT_PATHS,
        "module": module_name,
        "function": function_name,
        "args": list(args),
        "kwargs": kwargs,
    }
    runner_code = """
import importlib
import json
import sys

payload = json.loads(sys.argv[1])
for path in payload.get("paths", []):
    if path and path not in sys.path:
        sys.path.insert(0, path)

module = importlib.import_module(payload["module"])
function = getattr(module, payload["function"])
result = function(*payload.get("args", []), **payload.get("kwargs", {}))
print("__SCRAPE_RESULT__=" + json.dumps(result))
"""

    process = subprocess.Popen(
        [sys.executable, "-c", runner_code, json.dumps(runner_payload)],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    result_value: Any = None
    output_lines: list[str] = []
    assert process.stdout is not None

    try:
        for raw_line in process.stdout:
            line = raw_line.rstrip("\r\n")
            if line.startswith(SCRAPE_RESULT_MARKER):
                result_value = json.loads(line[len(SCRAPE_RESULT_MARKER) :])
                continue

            print(line)
            output_lines.append(line)
    finally:
        process.stdout.close()

    return_code = process.wait()
    if return_code != 0:
        tail = "\n".join(output_lines[-10:])
        raise RuntimeError(
            f"Scrape subprocess failed for {module_name}.{function_name} with exit code {return_code}.\n{tail}"
        )

    if result_value is None:
        raise RuntimeError(f"Scrape subprocess for {module_name}.{function_name} did not return a result.")

    return int(result_value)


def _scrape_sector_news(sector_key: str) -> int:
    print("Scraping sector news: " + sector_key)
    saved = _run_scrape_subprocess(
        "pipelines.sectorNewsPipeline",
        "get_sector_news_from_rss",
        sector_key,
    )
    print(f"Finished scraping sector news for {sector_key}: saved {saved} article(s)")
    return saved


def _scrape_industry_news(industry_key: str) -> int:
    print("Scraping industry news: " + industry_key)
    saved = _run_scrape_subprocess(
        "pipelines.industryNewsPipeline",
        "get_industry_news",
        industry_key,
    )
    print(f"Finished scraping industry news for {industry_key}: saved {saved} article(s)")
    return saved


def _scrape_company_news(symbol: str) -> int:
    print("Scraping company news: " + symbol)
    saved = _run_scrape_subprocess(
        "pipelines.companyNewsPipeline",
        "get_company_news",
        symbol,
    )
    print(f"Finished scraping company news for {symbol}: saved {saved} article(s)")
    return saved


def run_news_collection_pipeline(
    *,
    top_sector_count: int = DEFAULT_TOP_SECTOR_COUNT,
    top_industry_count: int = DEFAULT_TOP_INDUSTRY_COUNT,
    top_company_count: int = DEFAULT_TOP_COMPANY_COUNT,
    ranking_max_age_days: int | None | object = RANKING_MAX_AGE_DAYS_UNSET,
) -> dict[str, Any]:
    """Run the scrape plus sector/industry/company pipeline end to end."""
    ranking_max_age_days = resolve_ranking_max_age_days(ranking_max_age_days)
    print("Scraping U.S. news from RSS")
    us_news_saved = _run_scrape_subprocess(
        "pipelines.USNewsPipeline",
        "get_us_news_from_rss",
    )
    print(f"Finished scraping U.S. news: saved {us_news_saved} article(s)")

    print("Scraping world news from RSS")
    world_news_saved = _run_scrape_subprocess(
        "pipelines.worldNewsPipeline",
        "get_world_news_from_rss",
    )
    print(f"Finished scraping world news: saved {world_news_saved} article(s)")

    print("Classifying US macro news to sectors")
    us_macro_pairs = classify_macro_news_to_sectors(news_scope="us")
    print(f"Finished classifying US macro news: {len(us_macro_pairs)} sector pair(s)")

    print("Classifying world macro news to sectors")
    world_macro_pairs = classify_macro_news_to_sectors(news_scope="world")
    print(f"Finished classifying world macro news: {len(world_macro_pairs)} sector pair(s)")

    rankings_before_sector_stage = get_current_rankings(
        top_sector_count=top_sector_count,
        top_industry_count=top_industry_count,
        ranking_max_age_days=ranking_max_age_days,
    )

    sector_results: list[dict[str, Any]] = []
    scrape_results = {
        "macro_news": {
            "us": us_news_saved,
            "world": world_news_saved,
        },
        "sectors": {},
        "industries": {},
        "companies": {},
    }

    top_sector_keys = [sector["sector_key"] for sector in rankings_before_sector_stage["top_sectors"]]
    for sector_key in top_sector_keys:
        sector_scrape_saved = _scrape_sector_news(sector_key)
        scrape_results["sectors"][sector_key] = sector_scrape_saved

        print("Processing Sectors: " + sector_key)
        sector_opportunist_result = classify_sector_articles(sector_key)
        industry_opportunist_result = classify_sector_articles_to_industries(sector_key)

        industry_results: list[dict[str, Any]] = []
        top_industry_rankings = _get_ranked_industries_for_sector(
            sector_key,
            top_industry_count=top_industry_count,
            ranking_max_age_days=ranking_max_age_days,
        )
        top_industry_keys = [industry["industry_key"] for industry in top_industry_rankings]
        for industry_key in top_industry_keys:
            industry_scrape_saved = _scrape_industry_news(industry_key)
            scrape_results["industries"][industry_key] = industry_scrape_saved

            print("Processing industry: " + industry_key)
            company_selection = collect_ranked_companies_for_industry(
                industry_key,
                top_company_count=top_company_count,
            )

            company_results: list[dict[str, Any]] = []
            for company in company_selection["selected_companies"]:
                company_scrape_saved = _scrape_company_news(company["symbol"])
                scrape_results["companies"][company["symbol"]] = company_scrape_saved

                print("Processing: " + company["symbol"])
                company_result = classify_company_articles(company["symbol"])
                company_results.append(company_result)
                print("Finished: " + company["symbol"])

            company_opportunist_summaries: list[dict[str, Any]] = []
            for result in company_results:
                summary = build_company_opportunist_summary(result)
                company_opportunist_summaries.append(summary)

            industry_results.append(
                {
                    "industry": company_selection["industry"],
                    "selected_companies": company_selection["selected_companies"],
                    "company_opportunist_summaries": company_opportunist_summaries,
                }
            )
            print("Finished industry:" + industry_key)

        sector_results.append(
            {
                "sector_key": sector_key,
                "top_industries": top_industry_rankings,
                "sector_opportunist_result": sector_opportunist_result,
                "industry_opportunist_result": industry_opportunist_result,
                "industries": industry_results,
            }
        )
        print("Finished Sector:" + sector_key)

    rankings_after_pipeline = get_current_rankings(
        top_sector_count=top_sector_count,
        top_industry_count=top_industry_count,
        ranking_max_age_days=ranking_max_age_days,
    )

    return {
        "top_sector_count": top_sector_count,
        "top_industry_count": top_industry_count,
        "top_company_count": top_company_count,
        "ranking_max_age_days": ranking_max_age_days,
        "pipeline_mode": "news_refresh",
        "macro_news_to_sectors": {
            "us": us_macro_pairs,
            "world": world_macro_pairs,
        },
        "scrape_results": scrape_results,
        "rankings": {
            "before_sector_stage": rankings_before_sector_stage,
            "after_pipeline": rankings_after_pipeline,
        },
        "sectors": sector_results,
    }


def run_agent_pipeline(
    *,
    top_sector_count: int = DEFAULT_TOP_SECTOR_COUNT,
    top_industry_count: int = DEFAULT_TOP_INDUSTRY_COUNT,
    top_company_count: int = DEFAULT_TOP_COMPANY_COUNT,
    ranking_max_age_days: int | None | object = RANKING_MAX_AGE_DAYS_UNSET,
) -> dict[str, Any]:
    """Backward-compatible combined pipeline entrypoint."""
    ranking_max_age_days = resolve_ranking_max_age_days(ranking_max_age_days)
    return run_news_collection_pipeline(
        top_sector_count=top_sector_count,
        top_industry_count=top_industry_count,
        top_company_count=top_company_count,
        ranking_max_age_days=ranking_max_age_days,
    )
