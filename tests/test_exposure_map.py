from __future__ import annotations

from contextlib import closing
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database as initialize_market_database
from news_db_modules.bootstrap import initialize_database as initialize_news_database
from api_support.exposure_map import build_exposure_map_payload, calculate_position_risk_score, validate_terra_response


class ExposureMapTests(unittest.TestCase):
    def test_enum_form_sell_side_has_the_same_risk_direction_as_a_plain_sell(self):
        plain_score, plain_details = calculate_position_risk_score(
            quantity=1,
            direction="positive",
            magnitude="medium",
            confidence="high",
            dte=14,
            implied_volatility=0.25,
            contract_type="call",
            order_side="sell",
        )
        enum_score, enum_details = calculate_position_risk_score(
            quantity=1,
            direction="positive",
            magnitude="medium",
            confidence="high",
            dte=14,
            implied_volatility=0.25,
            contract_type="call",
            order_side="OrderSide.SELL",
        )
        self.assertEqual(enum_score, plain_score)
        self.assertEqual(enum_details, plain_details)

    def _seed_article(self, db_path: Path) -> int:
        initialize_market_database(db_path=db_path)
        initialize_news_database(db_path=db_path)
        with closing(get_connection(db_path)) as conn, conn:
            sector_id = conn.execute(
                "INSERT INTO sectors (sector_key, name) VALUES ('technology', 'Technology')"
            ).lastrowid
            industry_id = conn.execute(
                "INSERT INTO industries (sector_id, industry_key, name) VALUES (?, 'semiconductors', 'Semiconductors')",
                (sector_id,),
            ).lastrowid
            conn.execute(
                "INSERT INTO companies (industry_id, symbol, name) VALUES (?, 'NVDA', 'NVIDIA')",
                (industry_id,),
            )
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

    def test_the_map_never_returns_the_article_text(self):
        # The public snapshot republishes this payload; the scraped body is the publisher's copyrighted text.
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "exposure.db"
            article_id = self._seed_article(db_path)
            body = "Full publisher paragraph that must stay private."
            with closing(get_connection(db_path)) as conn, conn:
                conn.execute("INSERT INTO news_article_content (article_id, body) VALUES (?, ?)", (article_id, body))
                payload = build_exposure_map_payload(article_id, conn=conn)

        self.assertNotIn("body", payload["article"])
        self.assertEqual("Export restrictions update", payload["article"]["title"])
        self.assertNotIn(body, json.dumps(payload))

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


if __name__ == "__main__":
    unittest.main()
