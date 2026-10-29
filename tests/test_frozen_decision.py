"""FrozenDecision: one manager decision's frozen inputs, read and hash-checked in one place."""
from __future__ import annotations

from contextlib import closing
import json
import unittest

from db_common import get_connection
from market_db_modules.candidate_lifecycle import record_candidate_snapshot, stage_payloads
from market_db_modules.foundation_store import SnapshotHashMismatch
from market_db_modules.llm_replay_store import FrozenDecision, frozen_manager_input

from tests.db_fixtures import temporary_database

MANIFEST = {"pipeline_version": "fixture", "selector_config": {"ALLOW_RISKY_SIMPLE_FALLBACK": False}}
MANAGER_INPUT = {"company": {"symbol": "MSFT"}, "market_context": {"option_market": {"contracts": ["pre-decision"]}}}
STRATEGIST_INPUT = {"company": {"symbol": "MSFT"}, "views": {}}
BUCKET_CHAIN = {"contracts": ["re-fetched"]}


def history_row(candidate_id: str | None = None, replay_input=MANAGER_INPUT) -> dict:
    return {"id": 1, "candidate_id": candidate_id,
            "manager_input_json": json.dumps({"replay_input": replay_input} if replay_input is not None else {})}


def selection(decision: str, bucket: str = "45_60") -> dict:
    return {"recommendation": {"decision": decision, "target_dte_bucket": bucket},
            "market_context": {"option_market": BUCKET_CHAIN}}


class FrozenDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_database(self)

    def add_candidate(self, candidate_id: str = "candidate-1", **snapshots: dict) -> None:
        with closing(get_connection()) as conn, conn:
            conn.execute("INSERT INTO candidates (candidate_id, symbol, created_at, catalyst_ids_json, article_ids_json, "
                         "pipeline_version_json) VALUES (?, 'MSFT', '2026-03-02T15:00:00+00:00', '[]', '[]', ?)",
                         (candidate_id, json.dumps(MANIFEST)))
        for stage, payload in snapshots.items():
            record_candidate_snapshot({"candidate_id": candidate_id, "pipeline_version": MANIFEST}, stage, payload)

    def tamper(self, stage: str) -> None:
        with closing(get_connection()) as conn, conn:
            conn.execute("UPDATE candidate_snapshots SET snapshot_hash = 'tampered' WHERE stage = ?", (stage,))

    def test_a_candidate_decision_carries_every_frozen_input(self) -> None:
        self.add_candidate(pre_strategist=STRATEGIST_INPUT, pre_contract_selection=selection("call"))
        frozen = FrozenDecision.load(history_row("candidate-1"))
        self.assertEqual(frozen.manager_input, MANAGER_INPUT)
        self.assertEqual(frozen.strategist_input, STRATEGIST_INPUT)
        self.assertEqual(frozen.manifest, MANIFEST)
        self.assertEqual(frozen.bucket_chain("45_60"), BUCKET_CHAIN)

    def test_a_decision_without_a_candidate_has_only_its_manager_input(self) -> None:
        frozen = FrozenDecision.load(history_row())
        self.assertEqual(frozen.manager_input, MANAGER_INPUT)
        self.assertIsNone(frozen.strategist_input)
        self.assertIsNone(frozen.manifest)
        self.assertIsNone(frozen.bucket_chain("45_60"))

    def test_an_input_without_a_company_is_not_frozen(self) -> None:
        self.assertIsNone(frozen_manager_input(history_row(replay_input={"market_context": {}})))
        self.assertIsNone(frozen_manager_input(history_row(replay_input=None)))
        self.assertIsNone(frozen_manager_input({"manager_input_json": "not json"}))
        self.assertEqual(frozen_manager_input({"manager_input_json": {"replay_input": MANAGER_INPUT}}), MANAGER_INPUT)
        with self.assertRaises(ValueError):
            FrozenDecision.load(history_row(replay_input={}))

    def test_only_a_directional_selection_saves_its_chain(self) -> None:
        self.add_candidate(pre_contract_selection=selection("neither"))
        self.assertIsNone(FrozenDecision.load(history_row("candidate-1")).saved_chain)

    def test_another_bucket_has_no_saved_chain(self) -> None:
        self.add_candidate(pre_contract_selection=selection("put", bucket="45_60"))
        self.assertIsNone(FrozenDecision.load(history_row("candidate-1")).bucket_chain("20_30"))

    def test_a_tampered_snapshot_is_refused(self) -> None:
        self.add_candidate(pre_strategist=STRATEGIST_INPUT, pre_contract_selection=selection("call"))
        self.tamper("pre_contract_selection")
        with self.assertRaises(SnapshotHashMismatch):
            FrozenDecision.load(history_row("candidate-1"))
        with self.assertRaises(SnapshotHashMismatch):
            stage_payloads("candidate-1", ("pre_contract_selection",))
        self.assertEqual(stage_payloads("candidate-1", ("pre_strategist",)), {"pre_strategist": STRATEGIST_INPUT})


if __name__ == "__main__":
    unittest.main()
