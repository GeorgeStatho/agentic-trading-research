from __future__ import annotations

import json
from typing import Any

from agent_contracts import CompanyScopePayload
from db_helpers import get_all_companies
from strategist_payload_price import _build_company_price_context


def _serialize_company_scope(company: dict[str, Any]) -> CompanyScopePayload:
    company_record = _get_company_market_record(company)
    historical_price_data, trend_quality = _build_company_price_context(company["symbol"])
    return {
        "company_id": company["company_id"],
        "symbol": company["symbol"],
        "name": company["name"],
        "industry_id": company["industry_id"],
        "industry_key": company["industry_key"],
        "industry_name": company["industry_name"],
        "sector_id": company["sector_id"],
        "sector_key": company["sector_key"],
        "sector_name": company["sector_name"],
        "rating": company_record.get("rating") or "",
        "market_weight": company_record.get("market_weight"),
        "market_data": _deserialize_company_raw_json(company_record.get("raw_json")),
        "historical_price_data": historical_price_data,
        "trend_quality": trend_quality,
    }


def _deserialize_company_raw_json(raw_json: Any) -> dict[str, Any]:
    if isinstance(raw_json, dict):
        return raw_json
    if isinstance(raw_json, str):
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _get_company_market_record(company: dict[str, Any]) -> dict[str, Any]:
    company_id = int(company["company_id"])
    company_symbol = str(company["symbol"] or "").strip().upper()

    for candidate in get_all_companies():
        try:
            candidate_id = int(candidate.get("id"))
        except (TypeError, ValueError):
            candidate_id = -1
        if candidate_id == company_id:
            return candidate

    for candidate in get_all_companies():
        symbol = str(candidate.get("symbol") or "").strip().upper()
        if symbol == company_symbol:
            return candidate

    return {}
