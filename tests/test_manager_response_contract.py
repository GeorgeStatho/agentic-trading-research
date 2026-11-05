from __future__ import annotations

import unittest

from agent_stages.manager_normalization import normalize_recommendation
from services.options.option_dte_buckets import TIME_HORIZON_TO_DTE_BUCKET


class ManagerResponseContractTests(unittest.TestCase):
    def test_a_trade_with_bucket_none_gets_the_strategist_bucket(self) -> None:
        normalized = normalize_recommendation(
            {"decision": "call", "confidence": "high", "target_dte_bucket": "none", "reason": "Strong setup"},
            strategist_recommendation={"time_horizon": "short_term"},
        )
        self.assertEqual(normalized["target_dte_bucket"], TIME_HORIZON_TO_DTE_BUCKET["short_term"])

    def test_no_trade_keeps_bucket_none(self) -> None:
        normalized = normalize_recommendation(
            {"decision": "neither", "confidence": "low", "target_dte_bucket": "none", "reason": "Weak setup"})
        self.assertEqual(normalized["target_dte_bucket"], "none")


if __name__ == "__main__":
    unittest.main()
