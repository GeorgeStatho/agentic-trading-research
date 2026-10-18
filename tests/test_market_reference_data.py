from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db_common import get_connection, use_database
from market_db_modules.bootstrap import initialize_database
from market_db_modules.entities import add_company, add_industry, add_sector
from market_db_modules.loaders import ensure_sector_definitions
from market_db_modules.queries import get_all_companies


class MarketReferenceDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "reference.db"

    def _sector_keys(self) -> list[str]:
        with closing(get_connection(self.db_path)) as conn:
            return [r["sector_key"] for r in conn.execute("SELECT sector_key FROM sectors ORDER BY sector_key")]

    def test_ensure_sector_definitions_adds_missing_keys_then_is_idempotent(self):
        json_path = self.root / "sectors.json"
        json_path.write_text(json.dumps({"technology": {"name": "Technology"}, "energy": {"name": "Energy"}}))
        initialize_database(db_path=self.db_path)
        add_sector("technology", "Technology", db_path=self.db_path)

        ensure_sector_definitions(json_path, db_path=self.db_path)
        self.assertEqual(self._sector_keys(), ["energy", "technology"])

        with closing(get_connection(self.db_path)) as conn:
            ensure_sector_definitions(json_path, db_path=self.db_path)
            self.assertEqual(self._sector_keys(), ["energy", "technology"])
        with closing(get_connection(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sectors").fetchone()[0], 2)

    def test_ensure_sector_definitions_ignores_missing_empty_and_non_dict_files(self):
        empty = self.root / "empty.json"
        empty.write_text("  \n")
        non_dict = self.root / "list.json"
        non_dict.write_text("[1, 2]")
        for path in (self.root / "missing.json", empty, non_dict):
            ensure_sector_definitions(path, db_path=self.db_path)
            self.assertEqual(self._sector_keys(), [])

    def test_get_all_companies_joins_sector_and_industry(self):
        initialize_database(db_path=self.db_path)
        sector_id = add_sector("technology", "Technology", db_path=self.db_path)
        industry_id = add_industry(sector_id, "semiconductors", "Semiconductors", db_path=self.db_path)
        add_company(industry_id, "NVDA", "NVIDIA Corporation", db_path=self.db_path)

        with use_database(self.db_path):
            rows = get_all_companies()

        match = [r for r in rows if r["symbol"] == "NVDA"]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["industry_key"], "semiconductors")
        self.assertEqual(match[0]["sector_name"], "Technology")


if __name__ == "__main__":
    unittest.main()
