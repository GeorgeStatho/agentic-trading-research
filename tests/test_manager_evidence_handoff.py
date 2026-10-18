from __future__ import annotations

import unittest

from agent_stages.manager_runner import _apply_manager_evidence_payload  # noqa: E402


class ManagerEvidenceHandoffTests(unittest.TestCase):
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
