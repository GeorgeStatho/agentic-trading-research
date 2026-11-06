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

from db_common import get_connection, use_database
from market_db_modules.bootstrap import initialize_database
from services.gates.contract_policy import ContractPolicy
from services.core.trading_path import trading_path_hashes
from services.research.research_promotion import gate_code_hash, policy_hash, source_hash

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

    def _record_candidate(self, candidate_id: str, stage: str, *, occurred_at: str, shadow: int = 1,
                          reason: str = "gate_rejected") -> None:
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
                (candidate_id, "NVDA", stage, reason, "{}", "{}", "v2:test", occurred_at, shadow),
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
        expected = policy_hash("LIQUIDITY", ContractPolicy(), gate_code_hash("LIQUIDITY", trading_path_hashes()))
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

    def test_proof_is_found_in_the_current_database_when_no_path_is_given(self) -> None:
        self._record_report("report-1", [])
        expected = policy_hash("LIQUIDITY", ContractPolicy(), gate_code_hash("LIQUIDITY", trading_path_hashes()))
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO research_gate_promotions VALUES (?,?,?,?,?,?)",
                ("LIQUIDITY", expected, "report-1", RECENT, 120, 250.0),
            )

        with use_database(self.db):
            payload = build_v2_status_payload()
        self.assertTrue(_stage(payload, "liquidity", "LIQUIDITY")["enforcement_allowed"])

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

    def test_rejections_are_counted_by_reason_code(self) -> None:
        self._record_candidate("c1", "LIQUIDITY", occurred_at=RECENT, reason="liquidity_wide_spread")
        self._record_candidate("c2", "LIQUIDITY", occurred_at=RECENT, reason="liquidity_wide_spread")
        self._record_candidate("c3", "SUPPORT", occurred_at=RECENT, shadow=0, reason="insufficient_independent_roots")
        self._record_candidate("c4", "LIQUIDITY", occurred_at=STALE, reason="liquidity_wide_spread")
        self._record_candidate("discovery:c5", "LIQUIDITY", occurred_at=RECENT, reason="liquidity_wide_spread")

        self.assertEqual(self._payload()["rejection_reasons"], [
            {"stage": "LIQUIDITY", "reason_code": "liquidity_wide_spread", "shadow": True, "candidates": 2},
            {"stage": "SUPPORT", "reason_code": "insufficient_independent_roots", "shadow": False, "candidates": 1},
        ])

    def test_source_files_are_hashed_once_per_process(self) -> None:
        # A running process's code cannot change, and every 60s poll used to re-read ~280 files.
        from api_support import v2_status
        from services.core import trading_path

        v2_status._trading_path_hashes.cache_clear()
        self.addCleanup(v2_status._trading_path_hashes.cache_clear)
        with patch.object(trading_path, "trading_path_hashes", wraps=trading_path.trading_path_hashes) as hashes:
            first = self._payload()
            second = self._payload()
        self.assertEqual(hashes.call_count, 1)
        self.assertEqual(first["source_hash"], second["source_hash"])

    def test_a_failed_source_hash_is_retried_on_the_next_poll(self) -> None:
        from api_support import v2_status
        from services.core import trading_path

        v2_status._trading_path_hashes.cache_clear()
        self.addCleanup(v2_status._trading_path_hashes.cache_clear)
        with patch.object(trading_path, "trading_path_hashes", side_effect=[OSError("gone"), {"a.py": "h"}]):
            self.assertIsNone(self._payload()["source_hash"])
            self.assertIsNotNone(self._payload()["source_hash"])


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
        self.assertIsNone(payload["rejection_reasons"])
        self.assertIsNone(_stage(payload, "continuation", "CONTINUATION")["activity"])
        self.assertFalse(payload["research"]["available"])
        self.assertIn("Part 6 migration", payload["research"]["reason"])
        self.assertEqual(
            {item["status"] for item in payload["prerequisites"] if item["key"] != "regime_diversity"},
            {"missing"},
        )


if __name__ == "__main__":
    unittest.main()
