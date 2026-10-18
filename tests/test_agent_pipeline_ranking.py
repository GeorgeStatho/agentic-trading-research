from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

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

    def close(self):
        pass


class VerboseTestCase(unittest.TestCase):
    def log_pass(self, message: str) -> None:
        print(f"[PASS] {self.__class__.__name__}.{self._testMethodName}: {message}")


class SectorRankingTests(VerboseTestCase):
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
        self.log_pass("sector-interest loader kept only the recent row inside the configured max-age window")

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
        self.log_pass("sector ranking counted only high-confidence impacts when building scores and top-three output")


if __name__ == "__main__":
    unittest.main()
