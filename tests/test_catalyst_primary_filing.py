"""A company's own filing in the catalyst's window is a primary root for the provenance gate."""
from datetime import datetime, timedelta
import unittest

from tests.research_foundation_fixtures import database, ENTRY
from tests.test_company_evidence import COMPANY, guidance
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


if __name__ == '__main__':
    unittest.main()
