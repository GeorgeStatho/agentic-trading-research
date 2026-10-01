from __future__ import annotations

import unittest

from agent_stages.manager_runner import _apply_manager_evidence_payload  # noqa: E402


class ManagerEvidenceHandoffTests(unittest.TestCase):
    def test_uses_the_strategist_frozen_articles_and_grounding(self) -> None:
        payload = {
            "company": {"symbol": "NVDA"},
            "supporting_articles": {"article_summaries": []},
            "grounding": {"article_ids": []},
            "views": {},
        }
        frozen_evidence = {
            "company": {"symbol": "NVDA"},
            "supporting_articles": {
                "article_summaries": [
                    {"article_id": 101, "source": "Reuters", "evidence_score": 0.91},
                    {"article_id": 102, "source": "AP", "evidence_score": 0.84},
                ]
            },
            "grounding": {"article_ids": [101, 102]},
            "views": {"company_view": {"count": 2}},
        }

        self.assertTrue(_apply_manager_evidence_payload(payload, frozen_evidence))
        self.assertEqual(payload["grounding"]["article_ids"], [101, 102])
        self.assertEqual(len(payload["supporting_articles"]["article_summaries"]), 2)

        frozen_evidence["grounding"]["article_ids"].append(999)
        self.assertEqual(payload["grounding"]["article_ids"], [101, 102])

    def test_rejects_evidence_for_a_different_company(self) -> None:
        payload = {"company": {"symbol": "NVDA"}, "supporting_articles": {}, "grounding": {}}
        frozen_evidence = {
            "company": {"symbol": "AAPL"},
            "supporting_articles": {"article_summaries": [{"article_id": 1}]},
            "grounding": {"article_ids": [1]},
        }

        self.assertFalse(_apply_manager_evidence_payload(payload, frozen_evidence))
        self.assertEqual(payload["grounding"], {})


if __name__ == "__main__":
    unittest.main()
