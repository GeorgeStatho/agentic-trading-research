"""Plan B: the point-in-time replay benchmark. Slice B1 (model knowledge cutoffs and the contamination guard) is here.

Tests read `tests/fixtures/model_cutoffs.csv` through the module's `CUTOFFS_CSV` path, never the real table.
Modules under test are imported on use so each test fails on its own while they do not exist yet.
"""
from __future__ import annotations

from contextlib import closing
import csv
from datetime import date
import importlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from db_common import get_connection
from services import llm_backtest
from services.llm_direction_score import run_direction_score
from tests.test_llm_backtest import DatabaseFixture

FIXTURE_CSV = Path(__file__).resolve().parent / "fixtures" / "model_cutoffs.csv"
REAL_CSV = Path(__file__).resolve().parents[1] / "config" / "model_knowledge_cutoffs.csv"
NO_SUCH_CSV = Path(__file__).resolve().parent / "fixtures" / "does_not_exist.csv"


def cutoffs():
    return importlib.import_module("services.model_cutoffs")


class PatchedCutoffTable(unittest.TestCase):
    csv_path = FIXTURE_CSV

    def setUp(self) -> None:
        super().setUp()
        patcher = patch("services.model_cutoffs.CUTOFFS_CSV", self.csv_path)
        patcher.start()
        self.addCleanup(patcher.stop)


class KnowledgeCutoffTests(PatchedCutoffTable):
    def test_the_exact_model_id_matches(self) -> None:
        self.assertEqual(cutoffs().knowledge_cutoff("test-model"), date(2026, 1, 1))

    def test_a_dated_variant_matches_its_longest_prefix(self) -> None:
        # test-model-pro is also prefixed by test-model, whose cutoff is earlier.
        self.assertEqual(cutoffs().knowledge_cutoff("test-model-pro-0301"), date(2026, 3, 1))
        self.assertEqual(cutoffs().knowledge_cutoff("test-model-flash-001"), date(2026, 1, 1))

    def test_an_exact_id_beats_a_shorter_prefix(self) -> None:
        self.assertEqual(cutoffs().knowledge_cutoff("test-model-pro"), date(2026, 3, 1))

    def test_an_unlisted_model_has_no_cutoff(self) -> None:
        # "other-test-model" contains a listed id but does not start with it.
        for model in ("mystery-model", "other-test-model", "", "test"):
            with self.subTest(model=model):
                self.assertIsNone(cutoffs().knowledge_cutoff(model))

    def test_the_margin_is_thirty_days(self) -> None:
        self.assertEqual(cutoffs().CONTAMINATION_MARGIN_DAYS, 30)


class EligibilityTests(PatchedCutoffTable):
    # test-model's cutoff is 2026-01-01, so its 30-day margin ends on 2026-01-31.
    def test_the_margin_boundary_is_whole_days(self) -> None:
        cases = (
            ("2026-01-15T12:00:00+00:00", "pre_knowledge_cutoff"),
            ("2026-01-01T00:00:00+00:00", "pre_knowledge_cutoff"),
            ("2025-06-01T00:00:00+00:00", "pre_knowledge_cutoff"),
            ("2026-01-31T00:00:00+00:00", "pre_knowledge_cutoff"),
            ("2026-01-31T23:59:59+00:00", "pre_knowledge_cutoff"),
            ("2026-02-01T00:00:00+00:00", "eligible"),
            ("2026-03-02T15:00:00+00:00", "eligible"),
        )
        for decided_at, expected in cases:
            with self.subTest(decided_at=decided_at):
                self.assertEqual(cutoffs().eligible(decided_at, "test-model"), expected)

    def test_a_longer_prefix_moves_the_boundary(self) -> None:
        # test-model-pro-0301: cutoff 2026-03-01, margin ends 2026-03-31.
        self.assertEqual(cutoffs().eligible("2026-03-31T23:00:00+00:00", "test-model-pro-0301"), "pre_knowledge_cutoff")
        self.assertEqual(cutoffs().eligible("2026-04-01T00:00:00+00:00", "test-model-pro-0301"), "eligible")

    def test_an_unlisted_model_is_unknown(self) -> None:
        self.assertEqual(cutoffs().eligible("2026-09-01T00:00:00+00:00", "mystery-model"), "cutoff_unknown")


class RealCutoffTableTests(unittest.TestCase):
    """Structure only: which models are listed is a data decision this test does not pin."""

    def test_the_shipped_table_is_well_formed(self) -> None:
        with REAL_CSV.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, ["model", "knowledge_cutoff", "source"])
            rows = list(reader)
        for row in rows:
            with self.subTest(model=row["model"]):
                self.assertTrue(row["model"].strip())
                date.fromisoformat(row["knowledge_cutoff"])
                self.assertTrue(row["source"].startswith("http") or row["source"] == "release-date-proxy")
        self.assertEqual(len({row["model"] for row in rows}), len(rows), "duplicate model rows")


class ContaminationGuardTests(PatchedCutoffTable, DatabaseFixture):
    """`ReplayRun.build` (shared by the option backtest and the direction scorer) refuses decisions a model may remember."""

    def setUp(self) -> None:
        super().setUp()
        self.pre_id = self.add_decision("2026-01-15T15:00:00+00:00")
        self.last_excluded_id = self.add_decision("2026-01-31T23:59:00+00:00")
        self.first_eligible_id = self.add_decision("2026-02-01T00:00:00+00:00")

    def add_decision(self, at: str) -> int:
        with closing(get_connection(self.db_path)) as conn, conn:
            history_id = conn.execute("INSERT INTO manager_decision_history (symbol, decision_run_at, manager_decision) "
                                      "VALUES ('MSFT', ?, 'neither')", (at,)).lastrowid
            conn.execute("INSERT INTO manager_decision_payloads (manager_decision_history_id, manager_input_json, "
                         "manager_output_json) VALUES (?, ?, ?)",
                         (history_id, json.dumps({"replay_input": {"company": {"symbol": "MSFT", "at": at}}}),  # distinct: answers are cached by input
                          json.dumps({"recommendation": {"decision": "neither"}})))
        return history_id

    def build(self, model: str, **options) -> llm_backtest.ReplayRun:
        return llm_backtest.ReplayRun.build(model=model, decide=Mock(), db_path=self.db_path, **options)

    def summary(self, run: llm_backtest.ReplayRun) -> dict:
        return run.summary(llm_backtest.ReplayTally())

    def test_decisions_inside_the_margin_are_dropped_and_counted(self) -> None:
        run = self.build("test-model")
        self.assertEqual([d["id"] for d in run.decisions], [self.first_eligible_id, self.decision_id])
        self.assertEqual(self.summary(run)["excluded_pre_knowledge_cutoff"], 2)

    def test_the_contamination_record_names_the_cutoff_margin_and_source(self) -> None:
        self.assertEqual(self.summary(self.build("test-model"))["contamination"],
                         {"knowledge_cutoff": "2026-01-01", "margin_days": 30,
                          "source": "https://example.test/models/test-model"})

    def test_a_model_known_past_every_decision_replays_nothing(self) -> None:
        run = self.build("test-model-pro-0301")
        self.assertEqual(run.decisions, [])
        self.assertEqual(self.summary(run)["excluded_pre_knowledge_cutoff"], 4)

    def test_an_unknown_cutoff_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.build("mystery-model")

    def test_an_unknown_cutoff_can_be_allowed_and_then_nothing_is_excluded(self) -> None:
        run = self.build("mystery-model", allow_unknown_cutoff=True)
        self.assertEqual(len(run.decisions), 4)
        self.assertEqual(self.summary(run).get("excluded_pre_knowledge_cutoff", 0), 0)

    def test_the_override_does_not_disable_the_guard_for_a_known_model(self) -> None:
        run = self.build("test-model", allow_unknown_cutoff=True)
        self.assertEqual([d["id"] for d in run.decisions], [self.first_eligible_id, self.decision_id])

    def test_stored_bypasses_the_guard_without_reading_the_table(self) -> None:
        with patch("services.model_cutoffs.CUTOFFS_CSV", NO_SUCH_CSV):
            run = self.build(llm_backtest.STORED_MODEL)
        self.assertEqual(len(run.decisions), 4)
        summary = self.summary(run)
        self.assertEqual(summary.get("excluded_pre_knowledge_cutoff", 0), 0)
        self.assertIsNone(summary.get("contamination"))

    def test_the_direction_scorer_shares_the_guard(self) -> None:
        decide = Mock(return_value={"recommendation": {"decision": "neither", "confidence": "high"}})
        with self.assertRaises(ValueError):
            run_direction_score(model="mystery-model", decide=decide, db_path=self.db_path)
        result = run_direction_score(model="test-model", decide=decide, db_path=self.db_path)
        self.assertEqual(result["replay"]["excluded_pre_knowledge_cutoff"], 2)
        self.assertEqual(decide.call_count, 2)
        run_direction_score(model="mystery-model", allow_unknown_cutoff=True, decide=decide, db_path=self.db_path)
        self.assertEqual(decide.call_count, 6)

    def test_the_direction_scorer_takes_decision_ids(self) -> None:
        result = run_direction_score(model=llm_backtest.STORED_MODEL, decision_ids=[self.pre_id, self.decision_id],
                                     db_path=self.db_path)
        self.assertEqual(result["replay"]["source_decision_count"], 2)


if __name__ == "__main__":
    unittest.main()
