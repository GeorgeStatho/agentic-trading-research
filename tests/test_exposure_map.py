from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON_SCRIPTS_DIR = ROOT_DIR / "Python Scripts"
AGENT_CALLERS_DIR = PYTHON_SCRIPTS_DIR / "agentCallers"
DATA_DIR = ROOT_DIR / "Data"
for path in (ROOT_DIR, PYTHON_SCRIPTS_DIR, AGENT_CALLERS_DIR, DATA_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from db_helpers import get_connection, initialize_market_database, initialize_news_database
from api_support.exposure_map import (
    build_exposure_map_payload,
    calculate_position_risk_score,
    create_exposure_run,
    list_recent_exposure_articles,
    resolve_exposure_map_model,
    validate_terra_response,
)


class ExposureMapTests(unittest.TestCase):
    def test_model_defaults_follow_the_configured_provider(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai", "EXPOSURE_MAP_MODEL": ""}, clear=False):
            self.assertEqual(resolve_exposure_map_model(), "gpt-5.6-terra")
        with patch.dict(
            os.environ,
            {"LLM_PROVIDER": "vertex", "EXPOSURE_MAP_MODEL": "", "VERTEX_DEFAULT_MODEL": "gemini-test"},
            clear=False,
        ):
            self.assertEqual(resolve_exposure_map_model(), "gemini-test")
        with patch.dict(
            os.environ,
            {"LLM_PROVIDER": "ollama", "EXPOSURE_MAP_MODEL": "", "EXPOSURE_MAP_OLLAMA_MODEL": "qwen-test"},
            clear=False,
        ):
            self.assertEqual(resolve_exposure_map_model(), "qwen-test")

    def _seed_article(self, db_path: Path) -> int:
        initialize_market_database(db_path=db_path)
        initialize_news_database(db_path=db_path)
        with get_connection(db_path) as conn:
            sector_id = conn.execute(
                "INSERT INTO sectors (sector_key, name) VALUES ('technology', 'Technology')"
            ).lastrowid
            industry_id = conn.execute(
                "INSERT INTO industries (sector_id, industry_key, name) VALUES (?, 'semiconductors', 'Semiconductors')",
                (sector_id,),
            ).lastrowid
            company_id = conn.execute(
                "INSERT INTO companies (industry_id, symbol, name) VALUES (?, 'NVDA', 'NVIDIA')",
                (industry_id,),
            ).lastrowid
            article_id = conn.execute(
                """
                INSERT INTO news_articles (source, article_key, title, source_url, evidence_score, published_at)
                VALUES ('Test Wire', 'exposure-test-article', 'Export restrictions update', 'https://example.test/article', 0.91, ?)
                """,
                (datetime.now(timezone.utc).isoformat(),),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO sector_opportunist_impacts (
                    article_id, sector_id, confidence, impact_direction, impact_magnitude, reason
                ) VALUES (?, ?, 'high', 'negative', 'high', 'Stored sector evidence')
                """,
                (article_id, sector_id),
            )
        return int(article_id)

    def test_risk_score_uses_deterministic_inputs(self):
        score, inputs = calculate_position_risk_score(
            quantity=1,
            direction="negative",
            magnitude="low",
            confidence="low",
            dte=45,
            implied_volatility=None,
            contract_type="call",
            order_side="buy",
        )

        self.assertEqual(score, 22.5)
        self.assertTrue(inputs["is_adverse"])
        self.assertEqual(inputs["formula"], "position_exposure × impact_magnitude × confidence_weight × DTE/volatility_multiplier")

    def test_invalid_json_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            validate_terra_response(None, article_id=42, relationships=[], positions=[])

    def test_recent_exposure_queue_excludes_articles_outside_the_age_window(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "exposure-queue.sqlite3"
            initialize_market_database(db_path=db_path)
            initialize_news_database(db_path=db_path)
            now = datetime(2026, 7, 20, tzinfo=timezone.utc)
            with get_connection(db_path) as conn:
                fresh_id = conn.execute(
                    """
                    INSERT INTO news_articles (source, article_key, title, source_url, evidence_score, published_at)
                    VALUES ('Test Wire', 'fresh-queue-article', 'Fresh evidence', 'https://example.test/fresh', 0.60, ?)
                    """,
                    ((now - timedelta(days=9)).isoformat(),),
                ).lastrowid
                stale_id = conn.execute(
                    """
                    INSERT INTO news_articles (source, article_key, title, source_url, evidence_score, published_at)
                    VALUES ('Test Wire', 'stale-queue-article', 'Stale high-score evidence', 'https://example.test/stale', 0.99, ?)
                    """,
                    ((now - timedelta(days=11)).isoformat(),),
                ).lastrowid

            with patch("api_support.exposure_map.DB_PATH", db_path):
                result = list_recent_exposure_articles(now=now)

        article_ids = [article["id"] for article in result["articles"]]
        self.assertIn(int(fresh_id), article_ids)
        self.assertNotIn(int(stale_id), article_ids)

    def test_unknown_model_reference_is_rejected(self):
        response = {
            "article_summary": "A factual summary.",
            "edges": [{
                "from": {"type": "article", "id": 42},
                "to": {"type": "company", "id": "UNKNOWN"},
                "direction": "negative",
                "magnitude": "medium",
                "confidence": "medium",
                "mechanism": "Unsupported reference.",
                "evidence_article_ids": [42],
            }],
            "position_impacts": [],
            "uncertainties": [],
        }
        relationships = [{
            "type": "company", "id": "7", "symbol": "NVDA", "label": "NVIDIA",
            "direction": "negative", "magnitude": "medium", "confidence": "medium",
            "reason": "Stored evidence", "source": "test",
        }]
        with self.assertRaisesRegex(ValueError, "unknown entity"):
            validate_terra_response(response, article_id=42, relationships=relationships, positions=[])

    def test_analysis_succeeds_without_open_paper_positions(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "exposure.sqlite3"
            article_id = self._seed_article(db_path)
            model_response = json.dumps({
                "article_summary": "The article reports a policy change affecting technology.",
                "edges": [{
                    "from": {"type": "article", "id": article_id},
                    "to": {"type": "sector", "id": "1"},
                    "direction": "negative",
                    "magnitude": "high",
                    "confidence": "high",
                    "mechanism": "The stored sector relationship identifies technology exposure.",
                    "evidence_article_ids": [article_id],
                }],
                "position_impacts": [],
                "uncertainties": ["Company-specific exemptions are unknown."],
            })
            with patch("api_support.exposure_map.DB_PATH", db_path), patch(
                "api_support.exposure_map.get_model_client", return_value=object()
            ), patch("api_support.exposure_map.ask_llm_model", return_value=model_response):
                payload = create_exposure_run(article_id)
                loaded_payload = build_exposure_map_payload(article_id)

            self.assertFalse(payload["cached"])
            self.assertTrue(loaded_payload["has_run"])
            self.assertEqual(loaded_payload["position_impacts"], [])
            self.assertTrue(loaded_payload["no_trade_executed"])


if __name__ == "__main__":
    unittest.main()
