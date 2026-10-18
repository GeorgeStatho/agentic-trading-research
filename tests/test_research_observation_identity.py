from contextlib import closing
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from tests import research_foundation_fixtures as fixtures
from db_common import get_connection
from research_foundation_contracts import ResearchObservation
from market_db_modules.research_observations import (
    record_research_observation,
    get_research_observation,
    paired_champion_results,
)
from market_db_modules.research_runs import start_run, collect_run, read_run
from market_db_modules.research_reports import monthly_report, promote_champion
from services.research_contract import canonical, fingerprint
from services.research_promotion import champion_allows
from services.research_contract import ResearchPolicy


class ObservationIdentityTests(unittest.TestCase):
    def setUp(self):
        self.db = fixtures.database(self)
        self.manifest = fixtures.manifest()

    def run_fixture(self, manifest=None, count=1):
        return start_run(manifest or self.manifest, target_candidates=count,
                         now=fixtures.START, db_path=self.db)["run_id"]

    def collect(self, run_id):
        return collect_run(run_id, as_of=fixtures.END, db_path=self.db)

    def test_same_sources_produce_same_observation_identity(self):
        self.assertEqual(fixtures.observation(self.db), fixtures.observation(self.db))

    def test_distinct_source_snapshot_has_distinct_identity(self):
        first = fixtures.observation(self.db)
        second = fixtures.observation(self.db, source_snapshot={"evidence": [], "new_fact": 2})
        self.assertNotEqual(first, second)

    def test_future_evidence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "after its cutoff"):
            fixtures.observation(self.db, source_snapshot={"available_at": fixtures.END})

    def test_duplicate_evaluations_do_not_inflate_cohort(self):
        run_id = self.run_fixture(count=2)
        observation = fixtures.observation(self.db)
        fixtures.candidate(self.db, self.manifest, observation)
        fixtures.candidate(self.db, self.manifest, observation)
        self.assertEqual(self.collect(run_id)["candidates"], 1)

    def test_changed_evaluator_sources_pause_collection(self):
        run_id = self.run_fixture()
        with patch("services.research_versions.evaluator_source_hash", return_value="changed"):
            with self.assertRaisesRegex(ValueError, "evaluator changed"):
                self.collect(run_id)

    def test_observation_witness_cannot_be_replaced(self):
        run_id = self.run_fixture()
        fixtures.candidate(self.db, self.manifest, fixtures.observation(self.db))
        with closing(get_connection(self.db)) as conn, conn:
            row = conn.execute("SELECT * FROM candidate_snapshots WHERE stage='observation_input'").fetchone()
            envelope = json.loads(row["snapshot_json"])
            envelope["payload"]["invented"] = True
            conn.execute("UPDATE candidate_snapshots SET snapshot_json=?,snapshot_hash=? WHERE id=?",
                         (canonical(envelope), fingerprint(envelope), row["id"]))
        with self.assertRaisesRegex(ValueError, "original shared source"):
            self.collect(run_id)

    def test_research_execution_guard_is_preserved(self):
        self.assertFalse(champion_allows({"research_run_id": "challenger"}, db_path=self.db))


class ChampionReplacementTests(unittest.TestCase):
    def setUp(self):
        self.db = fixtures.database(self)

    def test_changed_strategy_replaces_champion_on_shared_frozen_observations(self):
        old_manifest, new_manifest = fixtures.manifest(), fixtures.manifest("challenger")
        old_run = start_run(old_manifest, target_candidates=100, now=fixtures.START, db_path=self.db)["run_id"]
        new_run = start_run(new_manifest, target_candidates=100, now=fixtures.START, db_path=self.db)["run_id"]
        for index in range(100):
            observation = fixtures.observation(self.db, index)
            fixtures.candidate(self.db, old_manifest, observation)
            fixtures.candidate(self.db, new_manifest, observation, selection=1)
        fixtures.prices(self.db, 100)
        reports = []
        for run_id in (old_run, new_run):
            collect_run(run_id, as_of=fixtures.END, db_path=self.db)
            reports.append(monthly_report(run_id, "all", as_of=fixtures.END, db_path=self.db))
        promote_champion(reports[0]["report_id"], db_path=self.db)
        result = promote_champion(reports[1]["report_id"], db_path=self.db)
        self.assertEqual(result["champion_run_id"], new_run)


class PairingRulesTests(unittest.TestCase):
    def setUp(self):
        self.run = {"evaluation_version": {"evaluator_hash": "evaluator", "protocol_version": "protocol"},
                    "policy": ResearchPolicy()}
        self.old = {"n_observed": 1, "observation_results": [
            {"observation_id": "observation:one", "candidate_id": "incumbent", "status": "complete", "hypothetical_pnl": 1}]}
        self.old["report_id"] = fingerprint(self.old)
        self.new = {"n_observed": 1, "observation_results": [
            {"observation_id": "observation:one", "candidate_id": "challenger", "status": "complete", "hypothetical_pnl": 2}]}

    def test_different_candidates_pair_on_observation(self):
        self.assertEqual(paired_champion_results(self.run, self.old, self.run, self.new), [1])

    def test_different_execution_assumptions_cannot_pair(self):
        different = {**self.run, "policy": replace(ResearchPolicy(), fee_per_contract_side=0)}
        with self.assertRaisesRegex(ValueError, "same evaluation policy"):
            paired_champion_results(self.run, self.old, different, self.new)

    def test_missing_outcome_cannot_be_counted_as_cash(self):
        self.new["observation_results"][0]["status"] = "missing_entry_quote"
        with self.assertRaisesRegex(ValueError, "unavailable observation outcomes"):
            paired_champion_results(self.run, self.old, self.run, self.new)

    def test_duplicate_observations_cannot_inflate_pairing(self):
        self.new["observation_results"].append(dict(self.new["observation_results"][0]))
        self.new["n_observed"] = 2
        with self.assertRaisesRegex(ValueError, "same complete shared"):
            paired_champion_results(self.run, self.old, self.run, self.new)


if __name__ == "__main__":
    unittest.main()
