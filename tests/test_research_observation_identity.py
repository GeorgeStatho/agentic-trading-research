from contextlib import closing
from dataclasses import replace
import json
import sqlite3
import unittest
from unittest.mock import patch

from tests import research_foundation_fixtures as fixtures
from db_common import get_connection
from research_foundation_contracts import ResearchObservation
from market_db_modules.research_observations import (
    record_research_observation, get_research_observation, bind_candidate_observation,
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

    def test_distinct_protocol_has_distinct_identity(self):
        self.assertNotEqual(fixtures.observation(self.db), fixtures.observation(self.db, protocol_version="different"))

    def test_future_evidence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "after its cutoff"):
            fixtures.observation(self.db, source_snapshot={"available_at": fixtures.END})

    def test_company_identity_is_checked(self):
        with self.assertRaisesRegex(ValueError, "company and symbol"):
            fixtures.observation(self.db, company_id=2)

    def test_two_manifests_enroll_same_observation_with_different_candidates(self):
        new_manifest = fixtures.manifest("challenger")
        first_run, second_run = self.run_fixture(), self.run_fixture(new_manifest)
        observation = fixtures.observation(self.db)
        first = fixtures.candidate(self.db, self.manifest, observation)
        second = fixtures.candidate(self.db, new_manifest, observation, selection=1)
        self.collect(first_run)
        self.collect(second_run)
        with closing(get_connection(self.db)) as conn:
            rows = conn.execute("SELECT observation_id,candidate_id FROM strategy_evaluations ORDER BY candidate_id").fetchall()
        self.assertEqual({(row[0], row[1]) for row in rows},
                         {(observation, first["candidate_id"]), (observation, second["candidate_id"])})

    def test_duplicate_evaluations_do_not_inflate_cohort(self):
        run_id = self.run_fixture(count=2)
        observation = fixtures.observation(self.db)
        fixtures.candidate(self.db, self.manifest, observation)
        fixtures.candidate(self.db, self.manifest, observation)
        self.assertEqual(self.collect(run_id)["candidates"], 1)

    def test_protocol_mismatch_does_not_enroll(self):
        run_id = self.run_fixture()
        observation = fixtures.observation(self.db, protocol_version="different")
        fixtures.candidate(self.db, self.manifest, observation)
        self.assertEqual(self.collect(run_id)["candidates"], 0)

    def test_later_run_cannot_enroll_historical_observation(self):
        run_id = start_run(self.manifest, now=fixtures.END, db_path=self.db)["run_id"]
        observation = fixtures.observation(self.db)
        context = fixtures.candidate(self.db, self.manifest, observation)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE candidates SET created_at=? WHERE candidate_id=?", (fixtures.END, context["candidate_id"]))
        result = collect_run(run_id, as_of="2026-09-12T20:00:00+00:00", db_path=self.db)
        self.assertEqual(result["candidates"], 0)

    def test_legacy_candidate_stays_unpaired(self):
        run_id = self.run_fixture()
        fixtures.candidate(self.db, self.manifest)
        self.collect(run_id)
        report = monthly_report(run_id, "all", as_of=fixtures.END, db_path=self.db)
        self.assertEqual((report["n_legacy_unpaired"], report["observation_results"]), (1, []))

    def test_unfinished_later_candidate_retains_observation_as_unavailable(self):
        run_id = self.run_fixture()
        observation = fixtures.observation(self.db)
        fixtures.candidate(self.db, self.manifest, observation, completed=False,
                           created_at="2026-09-02T20:00:10+00:00")
        self.collect(run_id)
        with closing(get_connection(self.db)) as conn:
            result = conn.execute("SELECT status FROM strategy_evaluations WHERE run_id=?", (run_id,)).fetchone()
        self.assertEqual(result["status"], "unavailable")

    def test_cannot_attach_observation_after_decision(self):
        context = fixtures.candidate(self.db, self.manifest)
        observation = fixtures.observation(self.db)
        with closing(get_connection(self.db)) as conn, conn, self.assertRaisesRegex(ValueError, "after candidate evaluation"):
            bind_candidate_observation(conn, context["candidate_id"], observation)

    def test_changed_strategy_sources_do_not_pause_recorded_evaluation(self):
        run_id = self.run_fixture()
        fixtures.candidate(self.db, self.manifest, fixtures.observation(self.db))
        with patch("market_db_modules.research_runs.source_hash", return_value="new-strategy-code"):
            self.assertEqual(self.collect(run_id)["candidates"], 1)

    def test_changed_evaluator_sources_pause_collection(self):
        run_id = self.run_fixture()
        with patch("services.research_versions.evaluator_source_hash", return_value="changed"):
            with self.assertRaisesRegex(ValueError, "evaluator changed"):
                self.collect(run_id)

    def test_frozen_evaluator_identity_cannot_be_deleted(self):
        self.run_fixture()
        with closing(get_connection(self.db)) as conn, conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM research_run_versions")

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

    def test_same_observation_cannot_mask_changed_market_inputs(self):
        run_id = self.run_fixture()
        fixtures.candidate(self.db, self.manifest, fixtures.observation(self.db))
        with closing(get_connection(self.db)) as conn, conn:
            row = conn.execute("SELECT * FROM candidate_snapshots WHERE stage='post_contract_selection'").fetchone()
            envelope = json.loads(row["snapshot_json"])
            envelope["payload"]["market_context"]["market_regime"]["state"] = "changed"
            conn.execute("UPDATE candidate_snapshots SET snapshot_json=?,snapshot_hash=? WHERE id=?",
                         (canonical(envelope), fingerprint(envelope), row["id"]))
        with self.assertRaisesRegex(ValueError, "shared source snapshot"):
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

    def test_same_ticker_or_candidate_cannot_substitute_for_shared_sources(self):
        self.new["observation_results"][0]["observation_id"] = "observation:different-sources"
        with self.assertRaisesRegex(ValueError, "same complete shared"):
            paired_champion_results(self.run, self.old, self.run, self.new)

    def test_different_protocol_cannot_pair(self):
        different = {**self.run, "evaluation_version": {"evaluator_hash": "evaluator", "protocol_version": "other"}}
        with self.assertRaisesRegex(ValueError, "same evaluator and observation protocol"):
            paired_champion_results(self.run, self.old, different, self.new)

    def test_different_evaluator_cannot_pair(self):
        different = {**self.run, "evaluation_version": {"evaluator_hash": "other", "protocol_version": "protocol"}}
        with self.assertRaisesRegex(ValueError, "same evaluator and observation protocol"):
            paired_champion_results(self.run, self.old, different, self.new)

    def test_different_execution_assumptions_cannot_pair(self):
        different = {**self.run, "policy": replace(ResearchPolicy(), fee_per_contract_side=0)}
        with self.assertRaisesRegex(ValueError, "same evaluation policy"):
            paired_champion_results(self.run, self.old, different, self.new)

    def test_missing_outcome_cannot_be_counted_as_cash(self):
        self.new["observation_results"][0]["status"] = "missing_entry_quote"
        with self.assertRaisesRegex(ValueError, "unavailable observation outcomes"):
            paired_champion_results(self.run, self.old, self.run, self.new)

    def test_legacy_report_does_not_gain_inferred_observations(self):
        legacy = {"n_observed": 1}
        legacy["report_id"] = fingerprint(legacy)
        with self.assertRaisesRegex(ValueError, "same complete shared"):
            paired_champion_results(self.run, legacy, self.run, self.new)

    def test_duplicate_observations_cannot_inflate_pairing(self):
        self.new["observation_results"].append(dict(self.new["observation_results"][0]))
        self.new["n_observed"] = 2
        with self.assertRaisesRegex(ValueError, "same complete shared"):
            paired_champion_results(self.run, self.old, self.run, self.new)


if __name__ == "__main__":
    unittest.main()
