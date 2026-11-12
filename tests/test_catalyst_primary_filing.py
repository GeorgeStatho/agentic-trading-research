"""A company's own filing in the catalyst's window is a primary root for the provenance gate."""
from datetime import datetime, timedelta
import unittest

from tests.research_foundation_fixtures import database, ENTRY
from tests.test_company_evidence import COMPANY, guidance
from tests.test_yahoo_consensus import consensus_doc
from services.discovery.company_evidence_extraction import ingest_source
from services.discovery.company_evidence_packages import build_company_package
from services.discovery.company_evidence_policy import CompanyEvidencePolicy
from services.gates.catalyst_gates import evaluate_catalyst_gates, with_primary_filing


def shifted(hours):
    return (datetime.fromisoformat(ENTRY) + timedelta(hours=hours)).isoformat()


class PrimaryFilingTests(unittest.TestCase):
    def setUp(self):
        self.db = database(self)

    def file(self, published_at):
        document = ingest_source(COMPANY, guidance(at=published_at), published_at, db_path=self.db)[0]
        build_company_package(COMPANY, [document], published_at, CompanyEvidencePolicy(), db_path=self.db)

    def provenance(self, *, as_of=ENTRY):
        state = {'catalyst_id': 'catalyst:fixture', 'catalyst_family': 'earnings_positive', 'independent_root_count': 0,
                 'primary_source_present': False, 'evidence_score': .8, 'first_public_time': shifted(-2),
                 'latest_root_event_time': None, 'expected_half_life_minutes': 1440}
        state = with_primary_filing(state, 'NVDA', as_of=as_of, db_path=self.db)
        return evaluate_catalyst_gates(state, as_of=datetime.fromisoformat(as_of))['PROVENANCE']

    def test_filing_inside_event_window_counts_as_primary_root(self):
        self.file(shifted(-1))
        gate = self.provenance()
        self.assertIs(gate['would_allow'], True)
        self.assertEqual(gate['reason_codes'], ['pass_with_single_primary_root'])
        self.assertTrue(gate['metrics']['single_primary_exception'])

    def test_consensus_snapshot_is_not_a_primary_root(self):
        self.file(shifted(-30))  # a package needs a primary document; this one is outside the window
        at = shifted(-1)
        document = ingest_source(COMPANY, consensus_doc(at), at, db_path=self.db)[0]
        build_company_package(COMPANY, [document], at, CompanyEvidencePolicy(), db_path=self.db)
        gate = self.provenance()
        self.assertEqual(gate['reason_codes'], ['insufficient_independent_roots'])
        self.assertIsNone(gate['metrics']['primary_filing_document_id'])

    def test_filing_after_as_of_is_ignored(self):
        self.file(shifted(1))
        self.assertEqual(self.provenance()['reason_codes'], ['insufficient_independent_roots'])

    def test_filing_before_window_is_ignored(self):
        self.file(shifted(-30))
        self.assertEqual(self.provenance()['reason_codes'], ['insufficient_independent_roots'])

    def test_no_company_leaves_state_unchanged(self):
        state = {'catalyst_id': 'c', 'independent_root_count': 0, 'primary_source_present': False,
                 'first_public_time': shifted(-2)}
        self.assertEqual(with_primary_filing(state, 'NOPE', as_of=ENTRY, db_path=self.db), state)


class ProvenanceProofFileTests(unittest.TestCase):
    def test_a_company_evidence_change_resets_provenance_not_freshness(self):
        from services.core.trading_path import trading_path_hashes
        from services.research.research_promotion import gate_code_hash
        hashes = trading_path_hashes()
        edited = {**hashes, 'Data/market_db_modules/company_evidence.py': 'changed'}
        self.assertNotEqual(gate_code_hash('PROVENANCE', hashes), gate_code_hash('PROVENANCE', edited))
        self.assertEqual(gate_code_hash('FRESHNESS', hashes), gate_code_hash('FRESHNESS', edited))

    def test_every_proof_file_is_hashed_with_the_trading_path(self):
        from services.core.trading_path import trading_path_hashes
        from services.research.research_promotion import GATE_FILES
        hashes = trading_path_hashes()
        self.assertEqual({stage: [name for name in files if name not in hashes] for stage, files in GATE_FILES.items()
                          if any(name not in hashes for name in files)}, {})

    def test_shadow_gate_proofs_follow_the_files_they_read(self):
        from services.core.trading_path import trading_path_hashes
        from services.research.research_promotion import GATE_FILES, gate_code_hash
        hashes = trading_path_hashes()
        sides = {**hashes, 'Python Scripts/services/core/trade_sides.py': 'changed'}
        for stage in ('ENTRY_EXTENSION', 'REGIME_SPY_TREND'):
            with self.subTest(stage=stage):
                self.assertNotEqual(gate_code_hash(stage, hashes), gate_code_hash(stage, sides))
        executor = {**hashes, 'Python Scripts/services/trading/trade_executor.py': 'changed'}
        self.assertEqual(gate_code_hash('TRADABILITY', hashes), gate_code_hash('TRADABILITY', executor))
        for name in ('Python Scripts/services/options/reference_price.py',
                     'Python Scripts/agentCallers/agent_helpers/market_context/equities.py'):
            with self.subTest(name=name):
                self.assertIn(name, GATE_FILES['TRADABILITY'])


if __name__ == '__main__':
    unittest.main()
