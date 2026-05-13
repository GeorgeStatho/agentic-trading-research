from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from agent_analysis.industry_interest import (  # noqa: E402
    getIndustryScores,
    getTopThreeIndustries,
    get_processed_industry_interest,
)
from agent_analysis.sector_interest import (  # noqa: E402
    getSectorScores,
    getTopThreeSectors,
    get_processed_sector_interest,
)


class _FakeConnection:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_args, **_kwargs):
        return self

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class SectorRankingTests(unittest.TestCase):
    @patch("agent_analysis.sector_interest.initialize_news_database")
    @patch("agent_analysis.sector_interest.get_connection")
    def test_processed_sector_interest_respects_max_age_window(self, mock_get_connection, _mock_init_db):
        rows = [
            {
                "news_scope": "us",
                "article_id": 101,
                "sector_id": 1,
                "confidence": "high",
                "reason": "Recent high confidence impact",
                "impact_created_at": "2026-05-13T12:00:00+00:00",
                "sector_key": "technology",
                "sector_name": "Technology",
                "published_at": "2026-05-13T12:00:00+00:00",
                "processed_at": "2026-05-13T12:05:00+00:00",
                "model": "test-model",
            },
            {
                "news_scope": "world",
                "article_id": 102,
                "sector_id": 2,
                "confidence": "high",
                "reason": "Old high confidence impact",
                "impact_created_at": "2026-05-01T12:00:00+00:00",
                "sector_key": "energy",
                "sector_name": "Energy",
                "published_at": "2026-05-01T12:00:00+00:00",
                "processed_at": "2026-05-01T12:05:00+00:00",
                "model": "test-model",
            },
        ]
        mock_get_connection.return_value = _FakeConnection(rows)

        processed = get_processed_sector_interest(
            end_time=datetime(2026, 5, 13, 13, 0, tzinfo=timezone.utc),
            max_age_days=3,
        )

        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["sector_key"], "technology")

    @patch("agent_analysis.sector_interest.get_processed_sector_interest")
    def test_sector_scores_and_top_three_only_count_high_confidence(self, mock_processed_interest):
        mock_processed_interest.return_value = [
            {"sector_key": "technology", "confidence": "high"},
            {"sector_key": "technology", "confidence": "medium"},
            {"sector_key": "energy", "confidence": "high"},
            {"sector_key": "healthcare", "confidence": "high"},
            {"sector_key": "financials", "confidence": "high"},
            {"sector_key": "consumer", "confidence": "high"},
        ]

        scores = getSectorScores()
        top_three = getTopThreeSectors(scores)

        self.assertEqual(scores["technology"], 1)
        self.assertNotIn("medium", scores)
        self.assertEqual(top_three, [("technology", 1), ("energy", 1), ("healthcare", 1)])


class IndustryRankingTests(unittest.TestCase):
    @patch("agent_analysis.industry_interest.initialize_news_database")
    @patch("agent_analysis.industry_interest.find_sector")
    @patch("agent_analysis.industry_interest.get_connection")
    def test_processed_industry_interest_respects_sector_and_window(
        self,
        mock_get_connection,
        mock_find_sector,
        _mock_init_db,
    ):
        mock_find_sector.return_value = {"id": 19, "sector_key": "technology", "name": "Technology"}
        rows = [
            {
                "article_id": 201,
                "industry_id": 7,
                "confidence": "high",
                "impact_direction": "positive",
                "impact_magnitude": "major",
                "reason": "Recent semis impact",
                "impact_created_at": "2026-05-13T12:00:00+00:00",
                "industry_key": "semiconductors",
                "industry_name": "Semiconductors",
                "sector_id": 19,
                "sector_key": "technology",
                "sector_name": "Technology",
                "published_at": "2026-05-13T12:00:00+00:00",
                "processed_at": "2026-05-13T12:05:00+00:00",
                "model": "test-model",
            },
            {
                "article_id": 202,
                "industry_id": 8,
                "confidence": "high",
                "impact_direction": "positive",
                "impact_magnitude": "major",
                "reason": "Old software impact",
                "impact_created_at": "2026-05-02T12:00:00+00:00",
                "industry_key": "software",
                "industry_name": "Software",
                "sector_id": 19,
                "sector_key": "technology",
                "sector_name": "Technology",
                "published_at": "2026-05-02T12:00:00+00:00",
                "processed_at": "2026-05-02T12:05:00+00:00",
                "model": "test-model",
            },
        ]
        mock_get_connection.return_value = _FakeConnection(rows)

        processed = get_processed_industry_interest(
            "technology",
            end_time=datetime(2026, 5, 13, 13, 0, tzinfo=timezone.utc),
            max_age_days=3,
        )

        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["industry_key"], "semiconductors")

    @patch("agent_analysis.industry_interest.get_processed_industry_interest")
    def test_industry_scores_and_top_three_only_count_high_confidence(self, mock_processed_interest):
        mock_processed_interest.return_value = [
            {"industry_key": "semiconductors", "confidence": "high"},
            {"industry_key": "software", "confidence": "high"},
            {"industry_key": "software", "confidence": "medium"},
            {"industry_key": "hardware", "confidence": "high"},
            {"industry_key": "services", "confidence": "high"},
        ]

        scores = getIndustryScores("technology")
        top_three = getTopThreeIndustries(scores)

        self.assertEqual(scores["software"], 1)
        self.assertEqual(
            top_three,
            [("semiconductors", 1), ("software", 1), ("hardware", 1)],
        )


if __name__ == "__main__":
    unittest.main()
