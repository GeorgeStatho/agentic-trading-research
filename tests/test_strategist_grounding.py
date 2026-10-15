from __future__ import annotations

from contextlib import closing
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_stages.strategist_grounding import (
    build_strategist_grounding,
    validate_grounded_recommendation,
)
from agent_stages.strategist_normalization import normalize_recommendation
from db_common import get_connection
from market_db_modules.bootstrap import initialize_database as initialize_market_database
from news_db_modules.bootstrap import initialize_database as initialize_news_database
from news_db_modules.strategist_runs import record_strategist_run


def grounded_recommendation(*, article_id: int = 42, company_symbol: str = "AAPL") -> dict:
    return {
        "decision": "trade_candidate",
        "confidence": "high",
        "summary": "Fresh company evidence supports a positive directional thesis.",
        "why_now": "The evidence is recent and directly linked to the company.",
        "catalyst": ["A direct company catalyst is present."],
        "thesis": ["Company-specific evidence is favorable."],
        "risks": ["The catalyst may not persist."],
        "watchlist_reason": "",
        "grounded_company_symbol": company_symbol,
        "grounded_entity_ids": {"company_id": 7, "industry_id": 3, "sector_id": 1},
        "evidence_claims": [
            {"claim_type": "summary", "claim": "Positive company evidence", "evidence_article_ids": [article_id]},
            {"claim_type": "why_now", "claim": "Recent direct evidence", "evidence_article_ids": [article_id]},
            {"claim_type": "catalyst", "claim": "Company catalyst", "evidence_article_ids": [article_id]},
            {"claim_type": "thesis", "claim": "Favorable evidence", "evidence_article_ids": [article_id]},
            {"claim_type": "risk", "claim": "Catalyst durability", "evidence_article_ids": [article_id]},
        ],
    }


class StrategistGroundingTests(unittest.TestCase):
    def test_normalization_cites_derived_watchlist_reason_from_why_now(self):
        recommendation = grounded_recommendation()
        recommendation["decision"] = "watchlist"

        normalized = normalize_recommendation(recommendation)

        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["watchlist_reason"], recommendation["why_now"])
        watchlist_claims = [
            claim
            for claim in normalized["evidence_claims"]
            if claim["claim_type"] == "watchlist_reason"
        ]
        self.assertEqual(
            watchlist_claims,
            [
                {
                    "claim_type": "watchlist_reason",
                    "claim": recommendation["why_now"],
                    "evidence_article_ids": [42],
                }
            ],
        )
        validated = validate_grounded_recommendation(
            normalized,
            grounding={
                "company": {"symbol": "AAPL", "id": 7},
                "industry": {"id": 3},
                "sector": {"id": 1},
                "article_ids": [42],
            },
        )
        self.assertEqual(validated["watchlist_reason"], recommendation["why_now"])

    def test_normalization_cites_derived_watchlist_reason_from_summary_fallback(self):
        recommendation = grounded_recommendation()
        recommendation["decision"] = "watchlist"
        recommendation["why_now"] = ""
        recommendation["evidence_claims"][0]["evidence_article_ids"] = [43]

        normalized = normalize_recommendation(recommendation)

        self.assertIsNotNone(normalized)
        watchlist_claim = next(
            claim
            for claim in normalized["evidence_claims"]
            if claim["claim_type"] == "watchlist_reason"
        )
        self.assertEqual(watchlist_claim["claim"], recommendation["summary"])
        self.assertEqual(watchlist_claim["evidence_article_ids"], [43])

    def test_normalization_does_not_invent_watchlist_citation_without_cited_source(self):
        recommendation = grounded_recommendation()
        recommendation["decision"] = "watchlist"
        recommendation["evidence_claims"] = [
            claim
            for claim in recommendation["evidence_claims"]
            if claim["claim_type"] != "why_now"
        ]

        normalized = normalize_recommendation(recommendation)

        self.assertIsNotNone(normalized)
        self.assertFalse(
            any(claim["claim_type"] == "watchlist_reason" for claim in normalized["evidence_claims"])
        )
        with self.assertRaisesRegex(ValueError, "missing grounded"):
            validate_grounded_recommendation(
                normalized,
                grounding={
                    "company": {"symbol": "AAPL", "id": 7},
                    "industry": {"id": 3},
                    "sector": {"id": 1},
                    "article_ids": [42],
                },
            )

    def test_grounding_collects_visible_article_ids_and_relationships(self):
        grounding = build_strategist_grounding(
            {
                "company": {
                    "company_id": 7,
                    "symbol": "aapl",
                    "name": "Apple Inc.",
                    "industry_id": 3,
                    "industry_key": "hardware",
                    "sector_id": 1,
                    "sector_key": "technology",
                },
                "supporting_articles": {"article_summaries": [{"article_id": 42}], "full_articles": [{"article_id": 43}]},
                "views": {"company_view": {"agent_conclusions": [{"article_id": 44}]}},
            }
        )

        self.assertEqual(grounding["article_ids"], [42, 43, 44])
        self.assertEqual(grounding["company"]["symbol"], "AAPL")
        self.assertEqual(grounding["industry"]["id"], 3)
        self.assertEqual(grounding["sector"]["id"], 1)
        self.assertTrue(grounding["market_context_as_of"])

    def test_validation_rejects_unknown_article_and_company_references(self):
        grounding = {
            "company": {"symbol": "AAPL", "id": 7},
            "industry": {"id": 3},
            "sector": {"id": 1},
            "article_ids": [42],
        }
        with self.assertRaisesRegex(ValueError, "outside the grounded payload"):
            validate_grounded_recommendation(
                grounded_recommendation(article_id=99),
                grounding=grounding,
            )

        invalid_relationship = grounded_recommendation()
        invalid_relationship["grounded_entity_ids"]["sector_id"] = 99
        with self.assertRaisesRegex(ValueError, "sector outside"):
            validate_grounded_recommendation(invalid_relationship, grounding=grounding)
        with self.assertRaisesRegex(ValueError, "company outside"):
            validate_grounded_recommendation(
                grounded_recommendation(company_symbol="MSFT"),
                grounding=grounding,
            )

    def test_run_history_persists_full_replay_record(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "strategist-runs.sqlite3"
            initialize_market_database(db_path=db_path)
            initialize_news_database(db_path=db_path)
            with closing(get_connection(db_path)) as conn, conn:
                sector_id = conn.execute("INSERT INTO sectors (sector_key, name) VALUES ('technology', 'Technology')").lastrowid
                industry_id = conn.execute("INSERT INTO industries (sector_id, industry_key, name) VALUES (?, 'hardware', 'Hardware')", (sector_id,)).lastrowid
                company_id = conn.execute("INSERT INTO companies (industry_id, symbol, name) VALUES (?, 'AAPL', 'Apple Inc.')", (industry_id,)).lastrowid
                run_id = record_strategist_run(
                    company_id=int(company_id),
                    model="test-model",
                    prompt_version="grounded-strategist-v1",
                    input_article_ids=[42],
                    input_payload={"grounding": {"article_ids": [42]}},
                    output_payload={"recommendation": grounded_recommendation()},
                    status="completed",
                    conn=conn,
                )
                run = conn.execute("SELECT * FROM strategist_runs WHERE id = ?", (run_id,)).fetchone()

        self.assertEqual(run["model"], "test-model")
        self.assertEqual(run["prompt_version"], "grounded-strategist-v1")
        self.assertEqual(run["input_article_ids_json"], "[42]")
        self.assertEqual(run["status"], "completed")
        self.assertTrue(run["created_at"])


if __name__ == "__main__":
    unittest.main()
