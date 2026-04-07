from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


AGENT_CALLERS_DIR = Path(__file__).resolve().parent
if str(AGENT_CALLERS_DIR) not in sys.path:
    sys.path.append(str(AGENT_CALLERS_DIR))

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "Data"
if str(DATA_DIR) not in sys.path:
    sys.path.append(str(DATA_DIR))

from db_helpers import get_all_companies, get_all_industries, list_company_news_articles
from db_helpers.market import list_companies_by_industry, list_industry_company_rankings


def _find_industry(industry_identifier: str) -> dict[str, Any] | None:
    needle = " ".join(str(industry_identifier or "").split()).strip().lower()
    if not needle:
        return None

    industries = get_all_industries()
    for industry in industries:
        if str(industry.get("industry_key") or "").strip().lower() == needle:
            return industry
    for industry in industries:
        if str(industry.get("name") or "").strip().lower() == needle:
            return industry
    return None


def _find_company(company_identifier: str) -> dict[str, Any] | None:
    needle = " ".join(str(company_identifier or "").split()).strip().lower()
    if not needle:
        return None

    companies = get_all_companies()
    for company in companies:
        if str(company.get("symbol") or "").strip().lower() == needle:
            return company
    for company in companies:
        if str(company.get("name") or "").strip().lower() == needle:
            return company
    return None


def _serialize_company_row(row: Any) -> dict[str, Any]:
    return {
        "company_id": row["id"] if "id" in row.keys() else row["company_id"],
        "symbol": row["symbol"],
        "name": row["name"],
        "rating": row["rating"],
        "market_weight": row["market_weight"],
    }


def _serialize_ranked_company_row(row: Any) -> dict[str, Any]:
    return {
        "rank": row["rank"],
        "ranking_type": row["ranking_type"],
        "company_id": row["company_id"],
        "symbol": row["symbol"],
        "name": row["name"],
        "rating": row["rating"],
        "market_weight": row["market_weight"],
    }


def get_industry_company_groups(industry_identifier: str) -> dict[str, Any]:
    industry = _find_industry(industry_identifier)
    if industry is None:
        raise ValueError(f"Industry not found for identifier: {industry_identifier}")

    top_companies = [
        _serialize_company_row(row)
        for row in list_companies_by_industry(industry["industry_key"])
    ]
    top_growth_companies = [
        _serialize_ranked_company_row(row)
        for row in list_industry_company_rankings(industry["industry_key"], "top_growth")
    ]
    top_performing_companies = [
        _serialize_ranked_company_row(row)
        for row in list_industry_company_rankings(industry["industry_key"], "top_performing")
    ]

    return {
        "industry": {
            "industry_id": industry["id"],
            "industry_key": industry["industry_key"],
            "industry_name": industry["name"],
            "sector_key": industry["sector_key"],
            "sector_name": industry["sector_name"],
            "symbol": industry.get("symbol"),
            "market_weight": industry.get("market_weight"),
        },
        "top_companies": top_companies,
        "top_growth_companies": top_growth_companies,
        "top_performing_companies": top_performing_companies,
    }


def get_company_linked_articles(company_identifier: str) -> dict[str, Any]:
    company = _find_company(company_identifier)
    if company is None:
        raise ValueError(f"Company not found for identifier: {company_identifier}")

    return {
        "company": {
            "company_id": company["id"],
            "symbol": company["symbol"],
            "name": company["name"],
            "industry_id": company["industry_id"],
            "industry_key": company["industry_key"],
            "industry_name": company["industry_name"],
            "sector_id": company["sector_id"],
            "sector_key": company["sector_key"],
            "sector_name": company["sector_name"],
        },
        "articles": list_company_news_articles(company["id"]),
    }


if __name__ == "__main__":
    print(get_industry_company_groups("semiconductors"))
