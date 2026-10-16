from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "Data"

import market_data.hydration as hydration  # noqa: E402
from db_common import use_database  # noqa: E402
from market_db_modules.entities import add_company, add_industry, add_sector  # noqa: E402
from market_db_modules.refresh_state import (  # noqa: E402
    get_market_data_refresh_state,
    record_market_data_refresh_state,
)
from market_db_modules.bootstrap import initialize_database  # noqa: E402


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

    def test_ensure_industry_market_data_refreshes_when_top_companies_hydration_is_stale(self):
        stale_timestamp = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        record_market_data_refresh_state(
            hydration.INDUSTRY_TOP_COMPANIES_REFRESH_RESOURCE,
            "semiconductors",
            last_hydrated_at=stale_timestamp,
            db_path=self.db_path,
        )

        with use_database(self.db_path), patch.object(hydration, "load_industry_from_yfinance") as loader:
            industry = hydration.ensure_industry_market_data("semiconductors")

        self.assertIsNotNone(industry)
        self.assertEqual(industry["industry_key"], "semiconductors")
        loader.assert_called_once_with("semiconductors")

        refresh_state = get_market_data_refresh_state(
            hydration.INDUSTRY_TOP_COMPANIES_REFRESH_RESOURCE,
            "semiconductors",
            db_path=self.db_path,
        )
        self.assertIsNotNone(refresh_state)
        self.assertNotEqual(refresh_state["last_hydrated_at"], stale_timestamp)


if __name__ == "__main__":
    unittest.main()
