from __future__ import annotations

from contextlib import closing
import json
import sqlite3
from pathlib import Path
from typing import Any

from db_common import DB_PATH, clean_text, coerce_float, get_connection
from .bootstrap import initialize_database
from .entities import add_company, add_industry, add_sector, replace_industry_company_rankings
from .shared import SECTOR_DEFINITIONS_PATH


def load_sector_definitions(
    sector_definitions: dict[str, dict[str, Any]],
    db_path: Path | str = DB_PATH,
) -> None:
    initialize_database(db_path=db_path)

    with closing(get_connection(db_path)) as conn, conn:
        for sector_key, payload in sector_definitions.items():
            if not isinstance(payload, dict):
                payload = {}

            normalized_sector_key = clean_text(sector_key)
            if normalized_sector_key is None:
                continue

            sector_name = (
                clean_text(payload.get("sector_name"))
                or clean_text(payload.get("name"))
                or normalized_sector_key.replace("-", " ").title()
            )
            add_sector(
                normalized_sector_key,
                sector_name,
                raw_json=payload,
                conn=conn,
            )


def load_sector_definitions_from_json(
    json_path: Path | str = SECTOR_DEFINITIONS_PATH,
    db_path: Path | str = DB_PATH,
) -> None:
    payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Top-level sector definitions payload must be a dictionary.")
    load_sector_definitions(payload, db_path=db_path)


def _load_companies_for_industry(
    conn: sqlite3.Connection,
    *,
    industry_id: int,
    industry_payload: dict[str, Any],
) -> dict[str, int]:
    companies_payload = industry_payload.get("companies", {})
    if not isinstance(companies_payload, dict):
        return {}

    company_ids_by_symbol: dict[str, int] = {}
    for company_symbol, company_payload in companies_payload.items():
        if not isinstance(company_payload, dict):
            continue

        normalized_symbol = clean_text(company_payload.get("symbol")) or clean_text(company_symbol)
        normalized_name = clean_text(company_payload.get("name")) or normalized_symbol
        if normalized_symbol is None or normalized_name is None:
            continue

        company_id = add_company(
            industry_id,
            symbol=normalized_symbol,
            name=normalized_name,
            rating=clean_text(company_payload.get("rating")),
            market_weight=coerce_float(company_payload.get("market weight") or company_payload.get("market_weight")),
            raw_json=company_payload,
            conn=conn,
        )
        company_ids_by_symbol[normalized_symbol] = company_id
    return company_ids_by_symbol


def _load_industry_rankings(
    conn: sqlite3.Connection,
    *,
    industry_id: int,
    industry_payload: dict[str, Any],
    company_ids_by_symbol: dict[str, int],
) -> None:
    for ranking_type, payload_key in (
        ("top_growth", "top_growth_companies"),
        ("top_performing", "top_performing_companies"),
    ):
        ranking_payload = industry_payload.get(payload_key, {})
        ranked_companies: list[tuple[int, int, Any | None]] = []
        if isinstance(ranking_payload, dict):
            for rank, (company_symbol, company_payload) in enumerate(ranking_payload.items(), start=1):
                if not isinstance(company_payload, dict):
                    continue

                normalized_symbol = clean_text(company_payload.get("symbol")) or clean_text(company_symbol)
                normalized_name = clean_text(company_payload.get("name")) or normalized_symbol
                if normalized_symbol is None or normalized_name is None:
                    continue

                company_id = company_ids_by_symbol.get(normalized_symbol)
                if company_id is None:
                    company_id = add_company(
                        industry_id,
                        symbol=normalized_symbol,
                        name=normalized_name,
                        rating=clean_text(company_payload.get("rating")),
                        market_weight=coerce_float(
                            company_payload.get("market weight") or company_payload.get("market_weight")
                        ),
                        raw_json=company_payload,
                        conn=conn,
                    )
                    company_ids_by_symbol[normalized_symbol] = company_id

                ranked_companies.append((company_id, rank, company_payload))

        replace_industry_company_rankings(
            industry_id,
            ranking_type,
            ranked_companies,
            conn=conn,
        )


def load_sector_tree(
    sector_tree: dict[str, dict[str, Any]],
    db_path: Path | str = DB_PATH,
) -> None:
    initialize_database(db_path=db_path)

    with closing(get_connection(db_path)) as conn, conn:
        for sector_key, sector_payload in sector_tree.items():
            industries_payload = sector_payload
            sector_name = sector_key.replace("-", " ").title()
            sector_raw = None

            if isinstance(sector_payload, dict) and "industries" in sector_payload:
                industries_payload = sector_payload.get("industries", {})
                sector_name = sector_payload.get("name", sector_name)
                sector_raw = sector_payload

            sector_id = add_sector(sector_key, sector_name, raw_json=sector_raw, conn=conn)

            if not isinstance(industries_payload, dict):
                continue

            for industry_key, industry_payload in industries_payload.items():
                if not isinstance(industry_payload, dict):
                    continue

                industry_id = add_industry(
                    sector_id,
                    industry_key=industry_key,
                    name=industry_payload.get("name", industry_key.replace("-", " ").title()),
                    symbol=industry_payload.get("symbol"),
                    market_weight=coerce_float(
                        industry_payload.get("market weight") or industry_payload.get("market_weight")
                    ),
                    raw_json=industry_payload,
                    conn=conn,
                )

                company_ids_by_symbol = _load_companies_for_industry(
                    conn,
                    industry_id=industry_id,
                    industry_payload=industry_payload,
                )
                _load_industry_rankings(
                    conn,
                    industry_id=industry_id,
                    industry_payload=industry_payload,
                    company_ids_by_symbol=company_ids_by_symbol,
                )


def load_sector_tree_from_json(
    json_path: Path | str,
    db_path: Path | str = DB_PATH,
) -> None:
    payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Top-level JSON payload must be a dictionary.")
    load_sector_tree(payload, db_path=db_path)
