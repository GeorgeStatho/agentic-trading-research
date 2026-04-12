from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    AGENT_CALLERS_DIR = Path(__file__).resolve().parents[1]
    if str(AGENT_CALLERS_DIR) not in sys.path:
        sys.path.append(str(AGENT_CALLERS_DIR))

WEBSCRAPING_DIR = Path(__file__).resolve().parents[2] / "Webscraping"
if str(WEBSCRAPING_DIR) not in sys.path:
    sys.path.append(str(WEBSCRAPING_DIR))

from _paths import DATA_DIR, add_agent_caller_paths


add_agent_caller_paths()

from agent_analysis.industry_interest import getIndustryScores, getTopThreeIndustries
from agent_analysis.sector_interest import getSectorScores, getTopThreeSectors
from agent_builders.company_opportunity import get_industry_company_groups
from agent_helpers.company_opportunist import get_company_opportunist_summary
from agent_stages.company_opportunist import classify_company_articles
from agent_stages.industry_opportunist import classify_sector_articles_to_industries
from agent_stages.macro_news_to_sectors import classify_macro_news_to_sectors
from agent_stages.sector_opportunist import classify_sector_articles
from db_helpers import DB_PATH, get_connection

DEFAULT_TOP_SECTOR_COUNT = 3
DEFAULT_TOP_INDUSTRY_COUNT = 3
DEFAULT_TOP_COMPANY_COUNT = 3
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SUBPROCESS_IMPORT_PATHS = [
    str(PROJECT_ROOT / "Python Scripts" / "Webscraping"),
    str(PROJECT_ROOT / "Python Scripts" / "agentCallers"),
]
SCRAPE_RESULT_MARKER = "__SCRAPE_RESULT__="

__all__ = [
    "collect_ranked_companies_for_industry",
    "build_company_opportunist_summary",
    "clear_current_pipeline_targets",
    "get_current_pipeline_targets",
    "get_current_rankings",
    "run_agent_pipeline",
]


def _configure_console_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
        force=True,
    )


def _slice_companies(companies: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return companies[: max(0, int(limit))]


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


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


def collect_ranked_companies_for_industry(
    industry_identifier: str,
    *,
    top_company_count: int = DEFAULT_TOP_COMPANY_COUNT,
) -> dict[str, Any]:
    company_groups = get_industry_company_groups(industry_identifier)
    top_companies = _slice_companies(
        company_groups.get("top_companies", []),
        top_company_count,
    )

    selected_companies = [
        {
            "company_id": int(company["company_id"]),
            "symbol": company["symbol"],
            "name": company["name"],
            "rating": company.get("rating"),
            "market_weight": company.get("market_weight"),
            "source_groups": ["top_companies"],
        }
        for company in top_companies
    ]

    return {
        "industry": company_groups["industry"],
        "selected_companies": selected_companies,
    }


def build_company_opportunist_summary(result: dict[str, Any]) -> dict[str, Any]:
    company = result.get("company", {})
    impacts = result.get("impacts", [])

    confidence_counts: dict[str, int] = {}
    direction_counts: dict[str, int] = {}
    magnitude_counts: dict[str, int] = {}
    reasons: list[str] = []

    for impact in impacts:
        confidence = str(impact.get("confidence") or "").strip().lower()
        impact_direction = str(impact.get("impact_direction") or "").strip().lower()
        impact_magnitude = str(impact.get("impact_magnitude") or "").strip().lower()
        reason = str(impact.get("reason") or "").strip()

        if confidence:
            confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
        if impact_direction:
            direction_counts[impact_direction] = direction_counts.get(impact_direction, 0) + 1
        if impact_magnitude:
            magnitude_counts[impact_magnitude] = magnitude_counts.get(impact_magnitude, 0) + 1
        if reason and reason not in reasons:
            reasons.append(reason)

    return {
        "company": {
            "company_id": company.get("company_id"),
            "symbol": company.get("symbol"),
            "name": company.get("name"),
            "industry_key": company.get("industry_key"),
            "sector_key": company.get("sector_key"),
        },
        "impact_count": len(impacts),
        "confidence_counts": confidence_counts,
        "direction_counts": direction_counts,
        "magnitude_counts": magnitude_counts,
        "sample_reasons": reasons[:3],
    }


def _get_top_sector_keys(*, top_sector_count: int) -> list[str]:
    return [
        sector_key
        for sector_key, _score in getTopThreeSectors(getSectorScores())[: max(0, int(top_sector_count))]
    ]


def _get_top_industry_keys(
    sector_key: str,
    *,
    top_industry_count: int,
) -> list[str]:
    return [
        industry_key
        for industry_key, _score in getTopThreeIndustries(getIndustryScores(sector_key))[: max(0, int(top_industry_count))]
    ]


def _get_ranked_sectors(*, top_sector_count: int) -> list[dict[str, Any]]:
    ranked = getTopThreeSectors(getSectorScores())[: max(0, int(top_sector_count))]
    return [
        {
            "sector_key": sector_key,
            "score": score,
        }
        for sector_key, score in ranked
    ]


def _get_ranked_industries_for_sector(
    sector_key: str,
    *,
    top_industry_count: int,
) -> list[dict[str, Any]]:
    ranked = getTopThreeIndustries(getIndustryScores(sector_key))[: max(0, int(top_industry_count))]
    return [
        {
            "industry_key": industry_key,
            "score": score,
        }
        for industry_key, score in ranked
    ]


def get_current_rankings(
    *,
    top_sector_count: int = DEFAULT_TOP_SECTOR_COUNT,
    top_industry_count: int = DEFAULT_TOP_INDUSTRY_COUNT,
) -> dict[str, Any]:
    ranked_sectors = _get_ranked_sectors(top_sector_count=top_sector_count)
    ranked_industries_by_sector = {
        sector["sector_key"]: _get_ranked_industries_for_sector(
            sector["sector_key"],
            top_industry_count=top_industry_count,
        )
        for sector in ranked_sectors
    }

    return {
        "top_sectors": ranked_sectors,
        "top_industries_by_sector": ranked_industries_by_sector,
    }


def get_current_pipeline_targets(
    *,
    top_sector_count: int = DEFAULT_TOP_SECTOR_COUNT,
    top_industry_count: int = DEFAULT_TOP_INDUSTRY_COUNT,
    top_company_count: int = DEFAULT_TOP_COMPANY_COUNT,
) -> dict[str, Any]:
    rankings = get_current_rankings(
        top_sector_count=top_sector_count,
        top_industry_count=top_industry_count,
    )
    top_sector_keys = [sector["sector_key"] for sector in rankings["top_sectors"]]

    top_industry_keys: list[str] = []
    selected_companies: list[dict[str, Any]] = []
    selected_company_ids: list[int] = []
    selected_company_symbols: list[str] = []

    for sector_key in top_sector_keys:
        industry_keys = [
            industry["industry_key"]
            for industry in rankings["top_industries_by_sector"].get(sector_key, [])
        ]
        top_industry_keys.extend(industry_keys)

        for industry_key in industry_keys:
            company_selection = collect_ranked_companies_for_industry(
                industry_key,
                top_company_count=top_company_count,
            )
            for company in company_selection["selected_companies"]:
                company_id = int(company["company_id"])
                if company_id in selected_company_ids:
                    continue
                selected_companies.append(company)
                selected_company_ids.append(company_id)
                selected_company_symbols.append(str(company["symbol"]))

    return {
        "rankings": rankings,
        "top_sector_keys": _dedupe_preserving_order(top_sector_keys),
        "top_industry_keys": _dedupe_preserving_order(top_industry_keys),
        "selected_companies": selected_companies,
        "selected_company_ids": selected_company_ids,
        "selected_company_symbols": _dedupe_preserving_order(selected_company_symbols),
    }


def _count_rows_for_ids(
    conn,
    table_name: str,
    column_name: str,
    values: list[int],
) -> int:
    if not values:
        return 0
    placeholders = ",".join("?" for _ in values)
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM {table_name} WHERE {column_name} IN ({placeholders})",
        tuple(values),
    ).fetchone()
    return int(row["c"]) if row is not None else 0


def _load_article_ids_for_target_rows(
    conn,
    *,
    impact_table: str,
    id_column: str,
    target_ids: list[int],
) -> list[int]:
    if not target_ids:
        return []
    placeholders = ",".join("?" for _ in target_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT article_id
        FROM {impact_table}
        WHERE {id_column} IN ({placeholders})
        ORDER BY article_id
        """,
        tuple(target_ids),
    ).fetchall()
    return [int(row["article_id"]) for row in rows]


def clear_current_pipeline_targets(
    *,
    top_sector_count: int = DEFAULT_TOP_SECTOR_COUNT,
    top_industry_count: int = DEFAULT_TOP_INDUSTRY_COUNT,
    top_company_count: int = DEFAULT_TOP_COMPANY_COUNT,
) -> dict[str, Any]:
    targets = get_current_pipeline_targets(
        top_sector_count=top_sector_count,
        top_industry_count=top_industry_count,
        top_company_count=top_company_count,
    )

    with get_connection(DB_PATH) as conn:
        sector_rows = conn.execute(
            """
            SELECT id, sector_key
            FROM sectors
            WHERE sector_key IN ({placeholders})
            ORDER BY id
            """.format(
                placeholders=",".join("?" for _ in targets["top_sector_keys"]) or "''"
            ),
            tuple(targets["top_sector_keys"]),
        ).fetchall() if targets["top_sector_keys"] else []
        sector_ids = [int(row["id"]) for row in sector_rows]

        industry_rows = conn.execute(
            """
            SELECT id, industry_key
            FROM industries
            WHERE industry_key IN ({placeholders})
            ORDER BY id
            """.format(
                placeholders=",".join("?" for _ in targets["top_industry_keys"]) or "''"
            ),
            tuple(targets["top_industry_keys"]),
        ).fetchall() if targets["top_industry_keys"] else []
        industry_ids = [int(row["id"]) for row in industry_rows]

        company_ids = [int(company_id) for company_id in targets["selected_company_ids"]]

        sector_article_ids = _load_article_ids_for_target_rows(
            conn,
            impact_table="sector_opportunist_impacts",
            id_column="sector_id",
            target_ids=sector_ids,
        )
        industry_article_ids = _load_article_ids_for_target_rows(
            conn,
            impact_table="industry_opportunist_impacts",
            id_column="industry_id",
            target_ids=industry_ids,
        )

        deleted_counts = {
            "sector_opportunist_article_processing": _count_rows_for_ids(
                conn,
                "sector_opportunist_article_processing",
                "article_id",
                sector_article_ids,
            ),
            "sector_opportunist_impacts": _count_rows_for_ids(
                conn,
                "sector_opportunist_impacts",
                "sector_id",
                sector_ids,
            ),
            "industry_opportunist_article_processing": _count_rows_for_ids(
                conn,
                "industry_opportunist_article_processing",
                "article_id",
                industry_article_ids,
            ),
            "industry_opportunist_impacts": _count_rows_for_ids(
                conn,
                "industry_opportunist_impacts",
                "industry_id",
                industry_ids,
            ),
            "company_opportunist_article_processing": _count_rows_for_ids(
                conn,
                "company_opportunist_article_processing",
                "company_id",
                company_ids,
            ),
            "company_opportunist_impacts": _count_rows_for_ids(
                conn,
                "company_opportunist_impacts",
                "company_id",
                company_ids,
            ),
        }

        if sector_article_ids:
            placeholders = ",".join("?" for _ in sector_article_ids)
            conn.execute(
                f"DELETE FROM sector_opportunist_article_processing WHERE article_id IN ({placeholders})",
                tuple(sector_article_ids),
            )
        if sector_ids:
            placeholders = ",".join("?" for _ in sector_ids)
            conn.execute(
                f"DELETE FROM sector_opportunist_impacts WHERE sector_id IN ({placeholders})",
                tuple(sector_ids),
            )

        if industry_article_ids:
            placeholders = ",".join("?" for _ in industry_article_ids)
            conn.execute(
                f"DELETE FROM industry_opportunist_article_processing WHERE article_id IN ({placeholders})",
                tuple(industry_article_ids),
            )
        if industry_ids:
            placeholders = ",".join("?" for _ in industry_ids)
            conn.execute(
                f"DELETE FROM industry_opportunist_impacts WHERE industry_id IN ({placeholders})",
                tuple(industry_ids),
            )

        if company_ids:
            placeholders = ",".join("?" for _ in company_ids)
            conn.execute(
                f"DELETE FROM company_opportunist_article_processing WHERE company_id IN ({placeholders})",
                tuple(company_ids),
            )
            conn.execute(
                f"DELETE FROM company_opportunist_impacts WHERE company_id IN ({placeholders})",
                tuple(company_ids),
            )

    return {
        "targets": targets,
        "deleted_counts": deleted_counts,
    }


def run_agent_pipeline(
    *,
    top_sector_count: int = DEFAULT_TOP_SECTOR_COUNT,
    top_industry_count: int = DEFAULT_TOP_INDUSTRY_COUNT,
    top_company_count: int = DEFAULT_TOP_COMPANY_COUNT,
) -> dict[str, Any]:
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

        print("Processing Sectors: "+sector_key)
        sector_opportunist_result = classify_sector_articles(sector_key)
        industry_opportunist_result = classify_sector_articles_to_industries(sector_key)

        industry_results: list[dict[str, Any]] = []
        top_industry_rankings = _get_ranked_industries_for_sector(
            sector_key,
            top_industry_count=top_industry_count,
        )
        top_industry_keys = [industry["industry_key"] for industry in top_industry_rankings]
        for industry_key in top_industry_keys:
            industry_scrape_saved = _scrape_industry_news(industry_key)
            scrape_results["industries"][industry_key] = industry_scrape_saved

            print("Processing industry: "+industry_key)
            company_selection = collect_ranked_companies_for_industry(
                industry_key,
                top_company_count=top_company_count,
            )

            company_results: list[dict[str, Any]] = []
            for company in company_selection["selected_companies"]:
                company_scrape_saved = _scrape_company_news(company["symbol"])
                scrape_results["companies"][company["symbol"]] = company_scrape_saved

                print("Processing: " +company["symbol"])
                company_result = classify_company_articles(company["symbol"])
                company_results.append(company_result)
                print("Finished: " +company["symbol"])

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
            print("Finished industry:"+industry_key)

        sector_results.append(
            {
                "sector_key": sector_key,
                "top_industries": top_industry_rankings,
                "sector_opportunist_result": sector_opportunist_result,
                "industry_opportunist_result": industry_opportunist_result,
                "industries": industry_results,
            }
        )
        print("Finished Sector:"+sector_key)

    rankings_after_pipeline = get_current_rankings(
        top_sector_count=top_sector_count,
        top_industry_count=top_industry_count,
    )

    return {
        "top_sector_count": top_sector_count,
        "top_industry_count": top_industry_count,
        "top_company_count": top_company_count,
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


if __name__ == "__main__":
    _configure_console_logging()
    clear_current_pipeline_targets()
    result = run_agent_pipeline()
    print(json.dumps(result, ensure_ascii=True, indent=2))

    output_path = DATA_DIR / "agent_pipeline_output.json"
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=True, indent=2)

    print(f"Saved agent pipeline output to {output_path}")
    #print(get_company_opportunist_summary("NVDA"))
