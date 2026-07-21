from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "Data"
AGENT_CALLERS_DIR = ROOT_DIR / "Python Scripts" / "agentCallers"
for path in (ROOT_DIR, DATA_DIR, AGENT_CALLERS_DIR):
    normalized = str(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

from db_common import get_connection  # noqa: E402
from market_db import (  # noqa: E402
    add_company,
    add_industry,
    add_sector,
    initialize_database,
    list_replayable_manager_decisions,
    record_manager_decision_history,
)
from offline_manager_evaluation import (  # noqa: E402
    build_replay_variants,
    calculate_replay_metrics,
    run_legacy_partial_replay,
    run_historical_outcome_audit,
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
        with get_connection(self.db_path) as conn:
            run_count = conn.execute("SELECT COUNT(*) FROM manager_replay_evaluations").fetchone()[0]
            result_count = conn.execute("SELECT COUNT(*) FROM manager_replay_results").fetchone()[0]
        self.assertEqual(run_count, 1)
        self.assertEqual(result_count, 2)

    def test_legacy_decisions_without_frozen_input_are_skipped(self) -> None:
        self._record(
            decision_time="2026-01-01T10:00:00+00:00",
            pnl_updated_at="2026-01-03T10:00:00+00:00",
            pnl=4.0,
            replay_input=False,
        )

        report = run_offline_manager_evaluation(
            model="test-model",
            db_path=self.db_path,
            replay_decider=lambda _: self.fail("Legacy records must not invoke the manager."),
        )

        self.assertEqual(report["replayed_decision_count"], 0)
        self.assertEqual(report["skipped"][0]["reason"], "missing_frozen_replay_input")

    def test_evaluation_bootstraps_an_empty_market_database(self) -> None:
        empty_db_path = Path(self.temp_dir.name) / "empty_market.db"

        report = run_offline_manager_evaluation(
            model="test-model",
            db_path=empty_db_path,
            persist=False,
            replay_decider=lambda _: self.fail("An empty database must not invoke the manager."),
        )

        self.assertEqual(report["source_decision_count"], 0)
        with get_connection(empty_db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'manager_decision_history'"
            ).fetchone()
        self.assertIsNotNone(exists)

    def test_historical_outcome_audit_scores_legacy_decisions_without_model_calls(self) -> None:
        self._record(
            decision_time="2026-01-01T10:00:00+00:00",
            pnl_updated_at="2026-01-03T10:00:00+00:00",
            pnl=4.0,
            replay_input=False,
            manager_decision="call",
            manager_confidence="high",
        )
        self._record(
            decision_time="2026-01-04T10:00:00+00:00",
            pnl_updated_at="2026-01-06T10:00:00+00:00",
            pnl=-2.0,
            replay_input=False,
            manager_decision="put",
            manager_confidence="low",
        )

        report = run_historical_outcome_audit(limit=10, db_path=self.db_path)

        self.assertTrue(report["offline_only"])
        self.assertFalse(report["model_invoked"])
        self.assertEqual(report["metrics"]["audited_decision_count"], 2)
        self.assertEqual(report["metrics"]["win_rate"], 0.5)
        self.assertEqual(report["metrics"]["false_positive_rate"], 0.5)
        self.assertEqual(report["metrics"]["confidence_buckets"]["high"]["count"], 1)
        self.assertEqual(report["metrics"]["confidence_buckets"]["low"]["count"], 1)
        with get_connection(self.db_path) as conn:
            modes = [
                row[0]
                for row in conn.execute(
                    "SELECT replay_mode FROM manager_replay_results ORDER BY id"
                ).fetchall()
            ]
        self.assertEqual(modes, ["historical_outcome_audit", "historical_outcome_audit"])

    def test_historical_outcome_audit_identifies_linked_live_orders(self) -> None:
        decision_id = self._record(
            decision_time="2026-01-01T10:00:00+00:00",
            pnl_updated_at="2026-01-03T10:00:00+00:00",
            pnl=-6.0,
        )
        with get_connection(self.db_path) as conn:
            execution_id = conn.execute(
                """
                INSERT INTO option_trade_executions (
                    company_id, order_id, underlying_symbol, option_symbol, decision,
                    expiration_date, order_qty, paper, order_status, order_side, submitted_at
                ) VALUES (?, 'live-replay-order', 'MSFT', 'MSFT260619C00450000', 'call',
                          '2026-06-19', 1, 0, 'filled', 'buy', '2026-01-01T10:05:00+00:00')
                """,
                (self.company_id,),
            ).lastrowid
            conn.execute(
                """
                UPDATE manager_decision_history
                SET trade_execution_order_id = 'live-replay-order', trade_execution_record_id = ?
                WHERE id = ?
                """,
                (execution_id, decision_id),
            )

        report = run_historical_outcome_audit(limit=10, db_path=self.db_path, persist=False)

        self.assertEqual(report["events"][0]["execution_mode"], "live")
        self.assertEqual(report["events"][0]["execution"]["status"], "filled")
        self.assertEqual(report["metrics"]["execution_modes"]["live"]["count"], 1)
        self.assertEqual(report["metrics"]["execution_modes"]["live"]["false_positive_rate"], 1.0)

    def test_legacy_partial_replay_uses_only_saved_snapshot_fields(self) -> None:
        self._record(
            decision_time="2026-01-01T10:00:00+00:00",
            pnl_updated_at="2026-01-03T10:00:00+00:00",
            pnl=-1.0,
            replay_input=False,
        )
        record_manager_decision_history(
            {
                "company_id": self.company_id,
                "symbol": "MSFT",
                "company_name": "Microsoft",
                "decision_run_at": "2026-01-10T10:00:00+00:00",
                "manager_decision": "call",
                "manager_confidence": "medium",
                "trade_executed": True,
                "latest_trade_pnl_pct": 3.0,
                "latest_trade_pnl_updated_at": "2026-01-12T10:00:00+00:00",
                "resolved_outcome_label": "closed_profit",
                "manager_input_json": {
                    "company": {"company_id": self.company_id, "symbol": "MSFT", "name": "Microsoft"},
                    "market_context": {"current_stock_price": {"available": True, "price": 400.0}},
                    "strategist_recommendation": {"decision": "trade_candidate", "confidence": "high"},
                    "article_references": [{"article_id": 22, "title": "Stored catalyst", "source": "Newswire"}],
                    "recent_manager_decision_history": [{"must_not": "be used"}],
                },
                "manager_output_json": {"recommendation": {"decision": "call"}},
            },
            db_path=self.db_path,
        )
        self._record(
            decision_time="2026-01-15T10:00:00+00:00",
            pnl_updated_at="2026-01-17T10:00:00+00:00",
            pnl=2.0,
            replay_input=True,
        )

        def decider(payload: dict[str, object]) -> dict[str, object]:
            self.assertEqual(payload["filters"]["replay_mode"], "legacy_partial")
            self.assertNotIn("recent_manager_decision_history", payload)
            self.assertEqual(payload["supporting_articles"]["article_summaries"][0]["article_id"], 22)
            self.assertEqual(payload["market_context"]["current_stock_price"]["price"], 400.0)
            return {
                "recommendation": {
                    "decision": "call",
                    "confidence": "medium",
                    "target_dte_bucket": "20_30",
                    "reason": "Snapshot-only replay",
                }
            }

        report = run_legacy_partial_replay(
            model="test-model",
            limit=10,
            autopsy_limit=3,
            autopsy_max_age_days=180,
            db_path=self.db_path,
            replay_decider=decider,
        )

        self.assertEqual(report["mode"], "legacy_partial")
        self.assertEqual(report["legacy_partial_replayed_count"], 1)
        self.assertIn("Do not combine", report["comparability"])
        self.assertTrue(any(item["reason"].startswith("exact_replay_available") for item in report["skipped"]))
        with get_connection(self.db_path) as conn:
            modes = [row[0] for row in conn.execute("SELECT replay_mode FROM manager_replay_results ORDER BY id").fetchall()]
        self.assertEqual(modes, ["legacy_partial"])


if __name__ == "__main__":
    unittest.main()
