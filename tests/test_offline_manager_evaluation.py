from __future__ import annotations

from contextlib import closing
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "Data"

from db_common import get_connection  # noqa: E402
from market_db_modules.entities import add_company, add_industry, add_sector  # noqa: E402
from market_db_modules.bootstrap import initialize_database  # noqa: E402
from market_db_modules.manager_replay import list_replayable_manager_decisions  # noqa: E402
from market_db_modules.history_records import record_manager_decision_history  # noqa: E402
from offline_manager_evaluation import (
    build_replay_variants,
    calculate_replay_metrics,
    run_offline_manager_evaluation,
)


class OfflineManagerEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "manager_replay_test.db"
        initialize_database(db_path=self.db_path, schema_path=DATA_DIR / "market_schema.sql")
        sector_id = add_sector("technology", "Technology", db_path=self.db_path)
        industry_id = add_industry(sector_id, "software", "Software", db_path=self.db_path)
        self.company_id = add_company(industry_id, "MSFT", "Microsoft", db_path=self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _replay_input(self) -> dict[str, object]:
        return {
            "company": {"company_id": self.company_id, "symbol": "MSFT", "name": "Microsoft"},
            "opportunist_rollup": {"company": {"article_count": 1}},
            "views": {},
            "supporting_articles": {"article_summaries": [{"article_id": 10, "title": "Stored evidence"}]},
            "market_context": {},
            "resolved_autopsy_insights": [],
            "strategist_recommendation": {"decision": "trade_candidate"},
        }

    def _record(
        self,
        *,
        decision_time: str,
        pnl_updated_at: str,
        pnl: float,
        replay_input: bool = True,
        manager_decision: str = "call",
        manager_confidence: str = "high",
    ) -> int:
        manager_input = {"company": {"symbol": "MSFT"}}
        if replay_input:
            manager_input["replay_input"] = self._replay_input()
        return record_manager_decision_history(
            {
                "company_id": self.company_id,
                "symbol": "MSFT",
                "company_name": "Microsoft",
                "decision_run_at": decision_time,
                "manager_decision": manager_decision,
                "manager_confidence": manager_confidence,
                "trade_executed": True,
                "latest_trade_pnl_pct": pnl,
                "outcome_status": "closed",
                "realized_pnl_pct": pnl,
                "closed_at": pnl_updated_at,
                "latest_trade_pnl_updated_at": pnl_updated_at,
                "resolved_outcome_label": "closed_profit" if pnl > 0 else "closed_loss",
                "manager_input_json": manager_input,
                "manager_output_json": {"recommendation": {"decision": "call"}},
            },
            db_path=self.db_path,
        )

    def test_replay_feedback_excludes_outcomes_not_known_at_decision_time(self) -> None:
        self._record(
            decision_time="2026-01-01T10:00:00+00:00",
            pnl_updated_at="2026-01-05T10:00:00+00:00",
            pnl=-4.0,
        )
        self._record(
            decision_time="2026-01-10T10:00:00+00:00",
            pnl_updated_at="2026-02-01T10:00:00+00:00",
            pnl=-2.0,
        )
        target_id = self._record(
            decision_time="2026-01-20T10:00:00+00:00",
            pnl_updated_at="2026-01-25T10:00:00+00:00",
            pnl=5.0,
        )
        target = next(
            dict(row)
            for row in list_replayable_manager_decisions(limit=10, db_path=self.db_path)
            if row["id"] == target_id
        )

        variants = build_replay_variants(
            target,
            autopsy_limit=10,
            autopsy_max_age_days=180,
            db_path=self.db_path,
        )

        self.assertIsNotNone(variants)
        _, feedback, autopsy_ids = variants
        self.assertEqual(len(feedback["resolved_autopsy_insights"]), 1)
        self.assertEqual(len(autopsy_ids), 1)
        self.assertEqual(
            feedback["resolved_autopsy_insights"][0]["decision_run_at"],
            "2026-01-01T10:00:00+00:00",
        )

    def test_metrics_do_not_assign_counterfactual_pnl(self) -> None:
        metrics = calculate_replay_metrics(
            [
                {
                    "replay_mode": "baseline",
                    "historical_decision": "call",
                    "replay_decision": "call",
                    "replay_confidence": "high",
                    "pnl_pct": 10.0,
                },
                {
                    "replay_mode": "baseline",
                    "historical_decision": "call",
                    "replay_decision": "put",
                    "replay_confidence": "high",
                    "pnl_pct": -8.0,
                },
                {
                    "replay_mode": "resolved_autopsy_feedback",
                    "historical_decision": "call",
                    "replay_decision": "call",
                    "replay_confidence": "medium",
                    "pnl_pct": -3.0,
                },
            ]
        )

        self.assertEqual(metrics["baseline"]["scored_historical_trade_count"], 1)
        self.assertEqual(metrics["baseline"]["counterfactual_unscored_count"], 1)
        self.assertEqual(metrics["baseline"]["win_rate"], 1.0)
        self.assertEqual(metrics["resolved_autopsy_feedback"]["false_positive_rate"], 1.0)

    def test_evaluation_persists_results_without_execution(self) -> None:
        self._record(
            decision_time="2026-01-01T10:00:00+00:00",
            pnl_updated_at="2026-01-03T10:00:00+00:00",
            pnl=4.0,
        )

        report = run_offline_manager_evaluation(
            model="test-model",
            limit=10,
            autopsy_limit=3,
            autopsy_max_age_days=180,
            db_path=self.db_path,
            replay_decider=lambda payload: {
                "recommendation": {
                    "decision": "call",
                    "confidence": "high" if payload["resolved_autopsy_insights"] else "medium",
                    "target_dte_bucket": "20_30",
                    "reason": "Replay only",
                }
            },
        )

        self.assertTrue(report["offline_only"])
        self.assertEqual(report["replayed_decision_count"], 1)
        with closing(get_connection(self.db_path)) as conn, conn:
            run_count = conn.execute("SELECT COUNT(*) FROM manager_replay_evaluations").fetchone()[0]
            result_count = conn.execute("SELECT COUNT(*) FROM manager_replay_results").fetchone()[0]
        self.assertEqual(run_count, 1)
        self.assertEqual(result_count, 2)


if __name__ == "__main__":
    unittest.main()
