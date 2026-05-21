from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "Data"
for path in (ROOT_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

import db_helpers.market as market_helpers  # noqa: E402
from market_db import (  # noqa: E402
    add_company,
    add_industry,
    add_sector,
    get_market_data_refresh_state,
    initialize_database,
    record_market_data_refresh_state,
)


class MarketDataHydrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "market_refresh_test.db"
        initialize_database(db_path=self.db_path, schema_path=DATA_DIR / "market_schema.sql")

        sector_id = add_sector("technology", "Technology", db_path=self.db_path)
        industry_id = add_industry(
            sector_id,
            "semiconductors",
            "Semiconductors",
            db_path=self.db_path,
        )
        add_company(
            industry_id,
            "NVDA",
            "NVIDIA Corporation",
            market_weight=10.0,
            db_path=self.db_path,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _patch_db_path(self):
        return patch.object(market_helpers, "DB_PATH", self.db_path)

    def test_ensure_industry_market_data_skips_refresh_when_top_companies_hydration_is_fresh(self):
        record_market_data_refresh_state(
            market_helpers.INDUSTRY_TOP_COMPANIES_REFRESH_RESOURCE,
            "semiconductors",
            last_hydrated_at=datetime.now(timezone.utc).isoformat(),
            db_path=self.db_path,
        )

        with self._patch_db_path(), patch.object(market_helpers, "load_industry_from_yfinance") as loader:
            industry = market_helpers.ensure_industry_market_data("semiconductors")

        self.assertIsNotNone(industry)
        self.assertEqual(industry["industry_key"], "semiconductors")
        loader.assert_not_called()

    def test_ensure_industry_market_data_refreshes_when_top_companies_hydration_is_stale(self):
        stale_timestamp = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        record_market_data_refresh_state(
            market_helpers.INDUSTRY_TOP_COMPANIES_REFRESH_RESOURCE,
            "semiconductors",
            last_hydrated_at=stale_timestamp,
            db_path=self.db_path,
        )

        with self._patch_db_path(), patch.object(market_helpers, "load_industry_from_yfinance") as loader:
            industry = market_helpers.ensure_industry_market_data("semiconductors")

        self.assertIsNotNone(industry)
        self.assertEqual(industry["industry_key"], "semiconductors")
        loader.assert_called_once_with("semiconductors")

        refresh_state = get_market_data_refresh_state(
            market_helpers.INDUSTRY_TOP_COMPANIES_REFRESH_RESOURCE,
            "semiconductors",
            db_path=self.db_path,
        )
        self.assertIsNotNone(refresh_state)
        self.assertNotEqual(refresh_state["last_hydrated_at"], stale_timestamp)


if __name__ == "__main__":
    unittest.main()
