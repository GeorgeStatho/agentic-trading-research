from contextlib import closing
from dataclasses import replace
import json
import sqlite3
import unittest
from unittest.mock import patch

from tests import research_foundation_fixtures as fixtures
from db_common import get_connection
from research_foundation_contracts import (
    CompanyFact, DiscoveryQueueItem, EvidenceDocument, EvidencePackage,
    ResearchSession, TradeEconomicsReport,
)
from market_db_modules.bootstrap import initialize_database
from market_db_modules.company_evidence import (
    record_evidence_document, record_company_fact, record_evidence_package,
    get_evidence_package, list_company_facts,
)
from market_db_modules.research_foundation import (
    enqueue_discovery, get_discovery_item, record_research_session,
    get_research_session, record_trade_economics_report, get_trade_economics_report,
)
from services.evidence_research_policy import foundation_manifest
from services.research_contract import canonical, fingerprint
from market_db_modules.research_runs import read_run


class EvidenceStorageTests(unittest.TestCase):
    def setUp(self):
        self.db = fixtures.database(self)
        self.document = EvidenceDocument(
            document_id="doc:1", provider="fixture", source_url="https://example.invalid/results",
            publisher="Issuer", document_type="earnings", text="Revenue was 100 million USD.",
            published_at=fixtures.START, first_observed_at=fixtures.START, ingested_at=fixtures.START)
        self.fact = CompanyFact(
            fact_id="fact:1", company_id=1, metric="revenue", kind="actual", value=100,
            unit="currency", currency="USD", scale=1_000_000, fiscal_period="2026Q2",
            period_type="quarter", accounting_basis="GAAP", document_id="doc:1",
            source_start=0, source_end=27, available_at=fixtures.START, extraction_version="fixture-v1")
        self.package = EvidencePackage(
            package_id="package:1", company_id=1, catalyst_id=fixtures.CATALYST,
            event_revision="revision:1", information_cutoff_time=fixtures.ENTRY,
            document_ids=("doc:1",), fact_ids=("fact:1",))
        record_evidence_document(self.document, db_path=self.db)
        record_company_fact(self.fact, db_path=self.db)

    def test_package_round_trip_and_repeated_write(self):
        record_evidence_package(self.package, db_path=self.db)
        record_evidence_package(self.package, db_path=self.db)
        self.assertEqual(get_evidence_package("package:1", db_path=self.db)["fact_ids"], ["fact:1"])

    def test_restatement_preserves_original_package(self):
        record_evidence_package(self.package, db_path=self.db)
        before = get_evidence_package("package:1", db_path=self.db)
        record_evidence_document(replace(self.document, document_id="doc:2", text="Revenue was 90 million USD.",
            supersedes_document_id="doc:1", published_at=fixtures.END,
            first_observed_at=fixtures.END, ingested_at=fixtures.END), db_path=self.db)
        record_company_fact(replace(self.fact, fact_id="fact:2", document_id="doc:2", value=90,
            source_end=26, available_at=fixtures.END, supersedes_fact_id="fact:1"), db_path=self.db)
        self.assertEqual(get_evidence_package("package:1", db_path=self.db), before)

    def test_future_facts_do_not_enter_historical_query(self):
        record_company_fact(replace(self.fact, fact_id="fact:later", available_at=fixtures.END), db_path=self.db)
        self.assertEqual(len(list_company_facts(1, as_of=fixtures.ENTRY, db_path=self.db)), 1)

    def test_future_fact_cannot_enter_package(self):
        record_company_fact(replace(self.fact, fact_id="fact:later", available_at=fixtures.END), db_path=self.db)
        with self.assertRaisesRegex(ValueError, "future fact"):
            record_evidence_package(replace(self.package, fact_ids=("fact:later",)), db_path=self.db)

    def test_other_company_fact_cannot_enter_package(self):
        record_company_fact(replace(self.fact, fact_id="fact:other", company_id=2), db_path=self.db)
        with self.assertRaisesRegex(ValueError, "another company"):
            record_evidence_package(replace(self.package, fact_ids=("fact:other",)), db_path=self.db)

    def test_existing_document_cannot_be_rewritten(self):
        with self.assertRaisesRegex(ValueError, "Immutable"):
            record_evidence_document(replace(self.document, text="Changed"), db_path=self.db)

    def test_sql_update_cannot_rewrite_immutable_fact(self):
        with closing(get_connection(self.db)) as conn, conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE company_facts SET metric='changed'")

    def test_missing_document_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown evidence_documents"):
            record_company_fact(replace(self.fact, fact_id="bad", document_id="absent"), db_path=self.db)

    def test_unknown_fact_requires_reason(self):
        with self.assertRaisesRegex(ValueError, "reason"):
            replace(self.fact, value=None)

    def test_nonfinite_fact_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            replace(self.fact, value=float("nan"))

    def test_source_span_is_validated(self):
        with self.assertRaisesRegex(ValueError, "source span"):
            record_company_fact(replace(self.fact, source_end=1000), db_path=self.db)

    def test_naive_timestamp_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            replace(self.document, published_at="2026-09-01")

    def test_duplicate_discovery_routes_share_item(self):
        item = DiscoveryQueueItem(company_id=1, catalyst_id=fixtures.CATALYST,
            evidence_revision="rev:1", purpose="entry", origin="news", created_at=fixtures.ENTRY)
        first = enqueue_discovery(item, db_path=self.db)
        second = enqueue_discovery(replace(item, origin="sector", created_at=fixtures.END), db_path=self.db)
        self.assertEqual(first, second)

    def test_new_revision_creates_new_queue_item(self):
        item = DiscoveryQueueItem(company_id=1, catalyst_id=fixtures.CATALYST,
            evidence_revision="rev:1", purpose="entry", origin="news", created_at=fixtures.ENTRY)
        first = enqueue_discovery(item, db_path=self.db)
        second = enqueue_discovery(replace(item, evidence_revision="rev:2"), db_path=self.db)
        self.assertNotEqual(first, second)

    def test_session_round_trip(self):
        record_evidence_package(self.package, db_path=self.db)
        queue_id = enqueue_discovery(DiscoveryQueueItem(company_id=1, catalyst_id=fixtures.CATALYST,
            evidence_revision=self.package.event_revision, purpose="entry", origin="news", created_at=fixtures.ENTRY), db_path=self.db)
        session = ResearchSession(session_id="session:1", queue_id=queue_id,
            initial_package_id="package:1", started_at=fixtures.ENTRY, deadline_at=fixtures.END,
            model="fixture", prompt_version="v1")
        record_research_session(session, db_path=self.db)
        self.assertEqual(get_research_session("session:1", db_path=self.db)["queue_id"], queue_id)

    def test_research_session_rejects_superseding_event_revision(self):
        record_evidence_package(self.package, db_path=self.db)
        queue_id = enqueue_discovery(DiscoveryQueueItem(company_id=1, catalyst_id=fixtures.CATALYST,
            evidence_revision="newer-revision", purpose="entry", origin="news", created_at=fixtures.ENTRY), db_path=self.db)
        session = ResearchSession(session_id="session:1", queue_id=queue_id,
            initial_package_id="package:1", started_at=fixtures.ENTRY, deadline_at=fixtures.END,
            model="fixture", prompt_version="v1")
        with self.assertRaisesRegex(ValueError, "revision differs"):
            record_research_session(session, db_path=self.db)

    def test_late_ingested_document_cannot_enter_earlier_package(self):
        record_evidence_document(replace(self.document, document_id="doc:late", ingested_at=fixtures.END), db_path=self.db)
        with self.assertRaisesRegex(ValueError, "future document"):
            record_evidence_package(replace(self.package, document_ids=("doc:late",), fact_ids=()), db_path=self.db)

    def test_economics_round_trip(self):
        record_evidence_package(self.package, db_path=self.db)
        context = fixtures.candidate(self.db, fixtures.manifest())
        report = TradeEconomicsReport(report_id="economics:1", candidate_id=context["candidate_id"],
            package_id="package:1", created_at=fixtures.ENTRY, valid_until=fixtures.END,
            forecast_version="v1", quote_snapshot={}, contract_outcomes=(), costs={},
            validity_conditions={}, coverage_status="unavailable")
        record_trade_economics_report(report, db_path=self.db)
        self.assertEqual(get_trade_economics_report("economics:1", db_path=self.db)["coverage_status"], "unavailable")

    def test_repeated_migration_has_no_foreign_key_errors(self):
        record_evidence_package(self.package, db_path=self.db)
        initialize_database(db_path=self.db)
        initialize_database(db_path=self.db)
        with closing(get_connection(self.db)) as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_facades_expose_foundation_functions(self):
        import market_db
        import db_helpers
        self.assertIs(market_db.record_evidence_package, db_helpers.record_evidence_package)


class MigrationAndManifestTests(unittest.TestCase):
    def test_legacy_records_and_hashes_survive_additive_migration(self):
        with patch("market_db_modules.research_foundation.migrate_research_foundation"):
            db = fixtures.database(self)
        manifest = fixtures.manifest()
        from services.research_contract import ResearchPolicy
        from dataclasses import asdict
        policy = asdict(ResearchPolicy())
        config_hash = fingerprint({"manifest": manifest, "policy": policy, "code_hash": "legacy",
            "kind": "frozen_forward", "started_at": fixtures.START, "target_candidates": 1})
        with closing(get_connection(db)) as conn, conn:
            conn.execute("INSERT INTO manager_decision_history(decision_run_at,manager_output_json) VALUES (?,?)",
                         (fixtures.START, '{"unchanged": true}'))
            conn.execute("INSERT INTO option_trade_executions(order_id,option_symbol) VALUES ('old-order','old-option')")
            conn.execute("""INSERT INTO candidates
                (candidate_id,symbol,created_at,catalyst_ids_json,article_ids_json,pipeline_version_json)
                VALUES ('old-candidate','NVDA',?,'[]','[]',?)""",
                         (fixtures.ENTRY, canonical(manifest)))
            conn.execute("""INSERT INTO research_runs VALUES ('old-run','frozen_forward',?,?,1,?,?,?,'legacy','sealed')""",
                         (fixtures.START, fixtures.START, canonical(manifest), canonical(policy), config_hash))
            conn.execute("INSERT INTO research_reports VALUES ('old-report','old-run',?,'all','{}')", (fixtures.END,))
            envelope = canonical({"payload": {"legacy": True}, "information_cutoff_time": fixtures.ENTRY})
            conn.execute("""INSERT INTO candidate_snapshots
                (candidate_id,stage,captured_at,information_cutoff_time,pipeline_version,
                 snapshot_version,snapshot_hash,snapshot_json)
                VALUES ('old-candidate','post_manager',?,?,'incumbent','v1',?,?)""",
                (fixtures.ENTRY, fixtures.ENTRY, fingerprint(json.loads(envelope)), envelope))
            tables = ("manager_decision_history", "option_trade_executions", "candidates",
                      "candidate_snapshots", "research_runs", "research_reports")
            before = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] for table in tables}
        initialize_database(db_path=db)
        initialize_database(db_path=db)
        with closing(get_connection(db)) as conn:
            after = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] for table in tables}
            read_run(conn, "old-run")
        self.assertEqual(after, before)

    def test_budget_changes_manifest_without_exposing_credentials(self):
        with patch.dict("os.environ", {"RESEARCH_MAX_TOOL_CALLS": "6", "OPENAI_API_KEY": "secret-fixture"}):
            first = foundation_manifest()
        with patch.dict("os.environ", {"RESEARCH_MAX_TOOL_CALLS": "7", "OPENAI_API_KEY": "secret-fixture"}):
            second = foundation_manifest()
        self.assertNotEqual(first["research_budget_hash"], second["research_budget_hash"])

    def test_credentials_are_absent_from_manifest(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret-fixture"}):
            self.assertNotIn("secret-fixture", json.dumps(foundation_manifest()))

    def test_foundation_schema_is_hashed(self):
        self.assertIn("Data/market_db_modules/research_foundation_schema.sql", foundation_manifest()["source_hashes"])


if __name__ == "__main__":
    unittest.main()
