from __future__ import annotations

import unittest
from unittest.mock import patch

from agentCallers import decision_pipeline as agent_runner_main  # noqa: E402


class AgentRunnerCompanyFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._market_regime_patcher = patch.object(
            agent_runner_main,
            "build_market_regime_snapshot",
            return_value={
                "as_of": "2026-07-21T15:00:00+00:00",
                "state": "trend_up",
                "macro_event_window": {"active": False},
                "policy_version": "market-regime-v1",
            },
        )
        self._market_regime_patcher.start()

    def tearDown(self) -> None:
        self._market_regime_patcher.stop()

    @patch.object(agent_runner_main, "record_manager_decision_history")
    @patch.object(agent_runner_main, "apply_deterministic_option_selection")
    @patch.object(agent_runner_main, "decide_company_option_position")
    @patch.object(agent_runner_main, "decide_company_purchase")
    def test_run_strategist_and_manager_records_manager_decision_history(
        self,
        mock_decide_company_purchase,
        mock_decide_company_option_position,
        mock_apply_deterministic_option_selection,
        mock_record_manager_decision_history,
    ) -> None:
        mock_decide_company_purchase.return_value = {
            "company": {"company_id": 1, "symbol": "AAPL", "name": "Apple"},
            "recommendation": {"decision": "trade_candidate", "confidence": "high"},
            "manager_evidence_payload": {
                "company": {"symbol": "AAPL"},
                "supporting_articles": {"article_summaries": [{"article_id": 101}]},
                "grounding": {"article_ids": [101]},
            },
            "strategist_run_reference": {
                "id": 42,
                "model": "test-model",
                "prompt_version": "grounded-strategist-v1",
                "input_article_ids": [101],
                "created_at": "2026-05-22T12:00:00+00:00",
            },
        }
        mock_decide_company_option_position.return_value = {
            "company": {"company_id": 1, "symbol": "AAPL", "name": "Apple"},
            "context_snapshot": {"article_summary_count": 2},
            "market_context": {"option_market": {"contract_count": 1}},
            "supporting_articles": {
                "article_summaries": [
                    {
                        "article_id": 101,
                        "title": "Apple catalyst",
                        "source": "Newswire",
                        "published_at": "2026-05-22T10:00:00+00:00",
                        "article_scope": "company",
                        "evidence_layers": ["company_view"],
                    }
                ],
                "full_articles": [],
            },
            "recent_manager_decision_history": [
                {
                    "decision_run_at": "2026-05-20T10:00:00+00:00",
                    "manager_decision": "call",
                    "manager_confidence": "medium",
                }
            ],
            "strategist_recommendation": {"decision": "trade_candidate", "confidence": "high"},
            "recommendation": {
                "decision": "call",
                "confidence": "high",
                "reason": "Strong setup",
                "target_dte_bucket": "20_30",
                "selected_option_id": "AAPL260619C00190000",
                "selected_expiration_date": "2026-06-19",
                "selected_strike_price": 190.0,
                "selected_option_source": "deterministic_high_confidence_hybrid_filtered_short_swing",
            },
            "selected_option": {"symbol": "AAPL260619C00190000"},
        }
        mock_apply_deterministic_option_selection.side_effect = lambda result: result
        mock_record_manager_decision_history.return_value = 77

        strategist_results, manager_results, _ = agent_runner_main._run_strategist_and_manager(["AAPL"])

        self.assertEqual(len(strategist_results), 1)
        self.assertEqual(len(manager_results), 1)
        self.assertEqual(manager_results[0]["manager_decision_history_id"], 77)
        mock_record_manager_decision_history.assert_called_once()
        recorded_payload = mock_record_manager_decision_history.call_args.args[0]
        self.assertEqual(recorded_payload["company_id"], 1)
        self.assertEqual(recorded_payload["symbol"], "AAPL")
        self.assertEqual(recorded_payload["manager_decision"], "call")
        self.assertEqual(recorded_payload["manager_confidence"], "high")
        self.assertEqual(recorded_payload["selected_option_symbol"], "AAPL260619C00190000")
        self.assertEqual(recorded_payload["manager_input_json"]["article_references"][0]["article_id"], 101)
        self.assertEqual(len(recorded_payload["manager_input_json"]["recent_manager_decision_history"]), 1)
        manager_input = mock_decide_company_option_position.call_args.kwargs["strategist_recommendation"]
        self.assertEqual(manager_input["strategist_run_reference"]["id"], 42)
        manager_evidence_payload = mock_decide_company_option_position.call_args.kwargs["manager_evidence_payload"]
        self.assertEqual(manager_evidence_payload["grounding"]["article_ids"], [101])

    @patch.object(agent_runner_main, "get_company_opportunist_summary")
    def test_filter_company_symbols_by_high_confidence_support_skips_under_supported_names(
        self,
        mock_get_company_opportunist_summary,
    ) -> None:
        mock_get_company_opportunist_summary.side_effect = [
            {
                "company": {"company_id": 1, "symbol": "AAPL", "name": "Apple"},
                "confidence_counts": {"high": 3, "medium": 1},
            },
            {
                "company": {"company_id": 2, "symbol": "MSFT", "name": "Microsoft"},
                "confidence_counts": {"high": 1},
            },
            {
                "company": {"company_id": 3, "symbol": "NVDA", "name": "NVIDIA"},
                "confidence_counts": {},
            },
        ]

        allowed_symbols, skipped_companies = agent_runner_main._filter_company_symbols_by_high_confidence_support(
            ["aapl", "msft", "nvda", "AAPL"],
            minimum_high_confidence_articles=2,
            max_age_days=5,
        )

        self.assertEqual(allowed_symbols, ["AAPL"])
        self.assertEqual(
            [{k: v for k, v in row.items() if k != "candidate_id"} for row in skipped_companies],
            [
                {
                    "symbol": "MSFT",
                    "company_id": 2,
                    "name": "Microsoft",
                    "high_confidence_article_count": 1,
                    "minimum_required_high_confidence_articles": 2,
                    "window_max_age_days": 5,
                    "skip_reason": "insufficient_high_confidence_articles",
                },
                {
                    "symbol": "NVDA",
                    "company_id": 3,
                    "name": "NVIDIA",
                    "high_confidence_article_count": 0,
                    "minimum_required_high_confidence_articles": 2,
                    "window_max_age_days": 5,
                    "skip_reason": "insufficient_high_confidence_articles",
                },
            ],
        )
        mock_get_company_opportunist_summary.assert_any_call("AAPL", max_age_days=5)
        mock_get_company_opportunist_summary.assert_any_call("MSFT", max_age_days=5)
        mock_get_company_opportunist_summary.assert_any_call("NVDA", max_age_days=5)


class CompanyFailureIsolationTests(unittest.TestCase):
    """One company's stage failure is recorded; the rest of the cycle continues."""

    def setUp(self) -> None:
        patches = {
            "build_market_regime_snapshot": {"return_value": {"state": "trend_up", "as_of": "2026-07-21T15:00:00+00:00"}},
            "record_manager_decision_history": {"return_value": 1},
            "decide_company_purchase": {"side_effect": self.strategist},
            "decide_company_option_position": {"side_effect": self.manager},
            "apply_deterministic_option_selection": {"side_effect": lambda result: result},
            # The failures are deliberate; keep their tracebacks out of the test output.
            "LOGGER": {},
        }
        for name, options in patches.items():
            patcher = patch.object(agent_runner_main, name, **options)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.failing = {"strategist": set(), "manager": set()}
        self.executed: list[str] = []

    def strategist(self, symbol, **kwargs):
        if symbol in self.failing["strategist"]:
            raise RuntimeError(f"strategist returned invalid JSON for {symbol}")
        return {"company": {"symbol": symbol}, "recommendation": {"decision": "trade_candidate", "confidence": "high"}}

    def manager(self, symbol, **kwargs):
        if symbol in self.failing["manager"]:
            raise TimeoutError("manager timed out")
        return {"company": {"symbol": symbol}, "recommendation": {"decision": "call", "confidence": "high"}}

    def run_cycle(self, symbols, on_manager_result=None):
        return agent_runner_main._run_strategist_and_manager(
            symbols, on_manager_result=on_manager_result or (lambda result: self.executed.append(result["company"]["symbol"])))

    def test_strategist_failure_does_not_stop_remaining_companies(self):
        self.failing["strategist"] = {"AAPL"}
        _, manager_results, failures = self.run_cycle(["AAPL", "MSFT"])
        self.assertEqual(([r["company"]["symbol"] for r in manager_results], [(f["symbol"], f["stage"]) for f in failures]),
                         (["MSFT"], [("AAPL", "STRATEGIST")]))

    def test_failed_manager_result_never_reaches_execution(self):
        self.failing["manager"] = {"AAPL"}
        _, _, failures = self.run_cycle(["AAPL", "MSFT"])
        self.assertEqual((self.executed, failures[0]["error_type"]), (["MSFT"], "TimeoutError"))

    def test_consecutive_failures_stop_the_cycle_early(self):
        symbols = ["A", "B", "C", "D", "E"]
        self.failing["strategist"] = set(symbols)
        _, _, failures = self.run_cycle(symbols)
        self.assertEqual((self.decide_company_purchase.call_count, [f["stage"] for f in failures]),
                         (3, ["STRATEGIST"] * 3 + ["NOT_RUN"] * 2))


if __name__ == "__main__":
    unittest.main()
