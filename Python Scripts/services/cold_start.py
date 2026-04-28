from __future__ import annotations

import logging
from typing import Any

from db_helpers import (
    ensure_all_sector_market_data,
    get_all_companies,
    get_all_industries,
    get_all_sectors,
    initialize_market_database,
    initialize_news_database,
)


class ColdStartSanityChecker:
    """Validate and optionally hydrate the market/news databases."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def run(self) -> dict[str, Any]:
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

        self._logger.info(
            "Cold-start sanity check detected an empty market DB. Bootstrapping all %s sectors from yfinance.",
            len(sectors),
        )
        hydrated_sectors = ensure_all_sector_market_data()
        if not hydrated_sectors:
            raise RuntimeError(
                "Cold-start sanity check failed: could not hydrate sectors from yfinance."
            )
        self._logger.info(
            "Cold-start full market bootstrap finished. Hydrated %s sectors from yfinance.",
            len(hydrated_sectors),
        )

        refreshed_industries = get_all_industries()
        refreshed_companies = get_all_companies()
        hydrated_sector_keys = [
            str(sector.get("sector_key") or "").strip()
            for sector in hydrated_sectors
            if str(sector.get("sector_key") or "").strip()
        ]
        result["industries_present"] = len(refreshed_industries)
        result["companies_present"] = len(refreshed_companies)
        result["hydrated_sector_count"] = len(hydrated_sector_keys)
        result["hydrated_sector_keys"] = hydrated_sector_keys

        if not refreshed_industries:
            raise RuntimeError(
                "Cold-start sanity check failed: sectors seeded but no industries were loaded from yfinance."
            )
        if not refreshed_companies:
            raise RuntimeError(
                "Cold-start sanity check failed: industries loaded but no companies were loaded from yfinance."
            )

        return result
