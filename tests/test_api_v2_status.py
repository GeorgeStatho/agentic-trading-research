from __future__ import annotations

import json
from contextlib import closing
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


try:
    from api_support.v2_status import build_v2_status_payload

    FLASK_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific test guard
    if exc.name != "flask":
        raise
    build_v2_status_payload = None
    FLASK_AVAILABLE = False

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database
from services.contract_policy import ContractPolicy
from services.research_promotion import policy_hash, source_hash

NOW = datetime.now(timezone.utc)
RECENT = (NOW - timedelta(days=1)).isoformat()
STALE = (NOW - timedelta(days=200)).isoformat()


def _insert_candidate(conn, candidate_id: str, symbol: str, created_at: str) -> None:
    conn.execute(
        """INSERT INTO candidates
           (candidate_id, symbol, created_at, catalyst_ids_json, article_ids_json, pipeline_version_json)
           VALUES (?,?,?,?,?,?)""",
        (candidate_id, symbol, created_at, "[]", "[]", "{}"),
    )


def _feature(payload: dict, key: str) -> dict:
    return next(item for item in payload["features"] if item["key"] == key)


def _stage(payload: dict, feature_key: str, stage: str) -> dict:
    return next(item for item in _feature(payload, feature_key)["stages"] if item["stage"] == stage)


@unittest.skipUnless(FLASK_AVAILABLE, "flask is not installed in this environment")
class V2StatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = str(Path(self.directory.name) / "v2-status.db")
        initialize_database(db_path=self.db)

    def _payload(self, **environment) -> dict:
        with patch.dict(os.environ, environment, clear=False):
            return build_v2_status_payload(db_path=self.db)

    def _record_candidate(self, candidate_id: str, stage: str, *, occurred_at: str, shadow: int = 1) -> None:
        with closing(get_connection(self.db)) as conn, conn:
            _insert_candidate(conn, candidate_id, "NVDA", occurred_at)
            conn.execute(
                """INSERT INTO candidate_snapshots
                   (candidate_id, stage, captured_at, information_cutoff_time, pipeline_version,
                    snapshot_version, snapshot_hash, snapshot_json)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (candidate_id, stage, occurred_at, occurred_at, "v2:test", "1", "hash", "{}"),
            )
            conn.execute(
                """INSERT INTO candidate_rejections
                   (candidate_id, symbol, stage, reason_code, metrics_json, thresholds_json,
                    config_version, occurred_at, shadow)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (candidate_id, "NVDA", stage, "gate_rejected", "{}", "{}", "v2:test", occurred_at, shadow),
            )

    def _record_report(self, report_id: str, gates: list[dict], *, run_id: str = "run-1",
                       code_hash: str | None = None, target: int = 100, cohort: int = 1) -> None:
        policy = {"minimum_promotion_samples": 100}
        report = {"n_observed": cohort, "paired_regimes": ["trend_up"], "gates": gates}
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO research_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, "frozen_forward", RECENT, RECENT, target, "{}", json.dumps(policy),
                 "config-hash", code_hash or source_hash(), "open"),
            )
            conn.execute(
                "INSERT INTO research_reports VALUES (?,?,?,?,?)",
                (report_id, run_id, RECENT, "all", json.dumps(report)),
            )

    def test_enforce_request_without_a_proof_is_reported_as_downgraded(self) -> None:
        payload = self._payload(V2_LIQUIDITY_MODE="enforce")

        liquidity = _feature(payload, "liquidity")
        self.assertEqual(liquidity["requested_mode"], "enforce")
        self.assertEqual(liquidity["effective_mode"], "shadow")
        self.assertTrue(liquidity["downgraded"])
        self.assertEqual(payload["mode_summary"]["downgraded"], 1)
        self.assertEqual(payload["mode_summary"]["enforcing"], 0)

    def test_matching_proof_reports_the_stage_as_enforceable(self) -> None:
        self._record_report("report-1", [])
        expected = policy_hash("LIQUIDITY", ContractPolicy(), source_hash())
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO research_gate_promotions VALUES (?,?,?,?,?,?)",
                ("LIQUIDITY", expected, "report-1", RECENT, 120, 250.0),
            )

        stage = _stage(self._payload(), "liquidity", "LIQUIDITY")
        self.assertTrue(stage["enforcement_allowed"])
        self.assertEqual(stage["proof"]["sample_size"], 120)
        self.assertEqual(stage["superseded_proofs"], 0)
        # The second stage of the same control has no proof of its own.
        self.assertFalse(_stage(self._payload(), "liquidity", "EXECUTION_LIQUIDITY")["enforcement_allowed"])

    def test_proof_recorded_under_other_thresholds_is_counted_as_superseded(self) -> None:
        self._record_report("report-1", [])
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO research_gate_promotions VALUES (?,?,?,?,?,?)",
                ("RISK_CLUSTER", "hash-from-an-earlier-policy", "report-1", RECENT, 150, 400.0),
            )

        stage = _stage(self._payload(), "risk_clusters", "RISK_CLUSTER")
        self.assertIsNone(stage["proof"])
        self.assertEqual(stage["superseded_proofs"], 1)
        self.assertFalse(stage["enforcement_allowed"])

    def test_shadow_verdicts_are_counted_inside_the_window(self) -> None:
        self._record_candidate("candidate-recent", "CONTINUATION", occurred_at=RECENT)
        self._record_candidate("candidate-old", "CONTINUATION", occurred_at=STALE)
        self._record_candidate("candidate-blocked", "REGIME", occurred_at=RECENT, shadow=0)

        payload = self._payload()
        self.assertTrue(payload["activity_available"])
        continuation = _stage(payload, "continuation", "CONTINUATION")["activity"]
        self.assertEqual(continuation["observed_candidates"], 1)
        self.assertEqual(continuation["shadow_blocks"], 1)
        self.assertEqual(_stage(payload, "regime", "REGIME")["activity"]["enforced_blocks"], 1)

    def test_position_exits_report_no_candidate_level_activity(self) -> None:
        """Exit comparisons are per position; a zero here would read as a quiet gate."""
        self._record_candidate("candidate-recent", "CONTINUATION", occurred_at=RECENT)

        self.assertIsNone(_stage(self._payload(), "position_exit", "POSITION_EXIT_V2")["activity"])

    def test_scenario_selection_reports_contract_disagreements(self) -> None:
        comparisons = [
            {"status": "complete", "baseline_option_symbol": "NVDA_A", "scenario_option_symbol": "NVDA_B"},
            {"status": "complete", "baseline_option_symbol": "NVDA_A", "scenario_option_symbol": "NVDA_A"},
            {"status": "insufficient_scenarios_or_inputs"},
        ]
        with closing(get_connection(self.db)) as conn, conn:
            for index, comparison in enumerate(comparisons):
                conn.execute(
                    "INSERT INTO contract_research_observations VALUES (?,?,?,?,?,?)",
                    (f"observation-{index}", None, "SCENARIO_SELECTION", RECENT,
                     "contract-execution-v2", json.dumps(comparison)),
                )

        detail = _feature(self._payload(), "scenario_selection")["detail"]
        self.assertEqual(detail["observations"], 3)
        self.assertEqual(detail["scored"], 2)
        self.assertEqual(detail["different_contract"], 1)

    def test_report_readiness_names_each_unmet_promotion_condition(self) -> None:
        gate = {
            "stage": "CONTINUATION",
            "policy_hash": "hash",
            "n_seen": 12,
            "n_evaluable": 8,
            "n_passed": 6,
            "n_rejected": 2,
            "n_missing_outcomes": 4,
            "regimes": ["trend_up"],
            "gate_value": -40.0,
            "coverage_complete": False,
            "production_selection_complete": True,
            "daily_coverage_complete": False,
        }
        self._record_report("report-1", [gate])

        run = self._payload()["research"]["runs"][0]
        reasons = run["latest_full_cohort_report"]["gates"][0]["blocking_reasons"]
        self.assertIn("counterfactual coverage is incomplete", reasons)
        self.assertIn("daily option marks are missing", reasons)
        self.assertIn("fewer than 100 evaluable observations", reasons)
        self.assertIn("fewer than two known regimes", reasons)
        self.assertIn("gate value is not positive", reasons)
        self.assertNotIn("some production contract selections are unknown", reasons)

    def test_a_source_change_marks_the_run_as_no_longer_collectable(self) -> None:
        self._record_report("report-1", [], code_hash="hash-from-before-the-edit")

        run = self._payload()["research"]["runs"][0]
        self.assertFalse(run["code_hash_matches"])
        self.assertEqual(run["enrolled_candidates"], 0)
        self.assertEqual(run["target_candidates"], 100)

    def test_strategy_change_does_not_mark_new_evaluator_as_stale(self) -> None:
        from market_db_modules.research_runs import start_run
        start_run({"pipeline_version": "fixture"}, db_path=self.db)
        with patch("api_support.v2_status._source_hash", return_value="changed-strategy"):
            run = self._payload()["research"]["runs"][0]
        self.assertEqual((run["code_hash_matches"], run["collection_version_matches"]), (False, True))

    def test_report_covering_less_than_the_cohort_is_flagged(self) -> None:
        self._record_candidate("candidate-recent", "CONTINUATION", occurred_at=RECENT)
        self._record_report("report-1", [], cohort=1)
        with closing(get_connection(self.db)) as conn, conn:
            for candidate_id in ("candidate-recent",):
                conn.execute(
                    "INSERT INTO research_candidates VALUES (?,?,?,?,?)",
                    ("run-1", candidate_id, RECENT, "input-hash", "{}"),
                )
            _insert_candidate(conn, "candidate-second", "AMD", RECENT)
            conn.execute(
                "INSERT INTO research_candidates VALUES (?,?,?,?,?)",
                ("run-1", "candidate-second", RECENT, "input-hash", "{}"),
            )

        report = self._payload()["research"]["runs"][0]["latest_full_cohort_report"]
        self.assertEqual(report["n_observed"], 1)
        self.assertFalse(report["covers_whole_cohort"])

    def test_prerequisites_report_the_inputs_the_rollout_waits_on(self) -> None:
        prerequisites = {item["key"]: item for item in self._payload()["prerequisites"]}

        self.assertEqual(prerequisites["candidate_stream"]["status"], "missing")
        self.assertEqual(prerequisites["option_marks"]["status"], "missing")
        self.assertEqual(prerequisites["regime_diversity"]["status"], "attention")

    def test_two_known_regimes_satisfy_the_diversity_prerequisite(self) -> None:
        with closing(get_connection(self.db)) as conn, conn:
            for state in ("trend_up", "range", "unknown"):
                conn.execute(
                    "INSERT INTO market_regime_runs (as_of, state, confidence, policy_version, snapshot_json) VALUES (?,?,?,?,?)",
                    (RECENT, state, "high", "market-regime-v2-scope", "{}"),
                )

        prerequisites = {item["key"]: item for item in self._payload()["prerequisites"]}
        self.assertEqual(prerequisites["regime_diversity"]["status"], "ok")
        self.assertEqual(prerequisites["regime_diversity"]["value"], "2 known in 30d")

    def test_an_invalid_mode_variable_is_reported_against_its_feature(self) -> None:
        payload = self._payload(V2_SCENARIO_MODE="enforced")

        scenario = _feature(payload, "scenario_selection")
        self.assertIsNone(scenario["effective_mode"])
        self.assertIn("shadow, enforce or disabled", scenario["configuration_error"])
        self.assertEqual(payload["mode_summary"]["misconfigured"], 4)


@unittest.skipUnless(FLASK_AVAILABLE, "flask is not installed in this environment")
class V2StatusWithoutMigrationTests(unittest.TestCase):
    """A database that predates the V2 tables reports absence, not quiet gates."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = str(Path(self.directory.name) / "empty.db")
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("CREATE TABLE placeholder (id INTEGER PRIMARY KEY)")

    def test_absent_tables_are_reported_rather_than_counted_as_zero(self) -> None:
        payload = build_v2_status_payload(db_path=self.db)

        self.assertFalse(payload["activity_available"])
        self.assertIsNone(_stage(payload, "continuation", "CONTINUATION")["activity"])
        self.assertFalse(payload["research"]["available"])
        self.assertIn("Part 6 migration", payload["research"]["reason"])
        self.assertEqual(
            {item["status"] for item in payload["prerequisites"] if item["key"] != "regime_diversity"},
            {"missing"},
        )


if __name__ == "__main__":
    unittest.main()
