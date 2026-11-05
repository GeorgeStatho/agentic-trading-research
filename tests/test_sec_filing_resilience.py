"""One oversize or failing SEC filing must not wipe out a company's evidence.

Large filers (ADP, MSFT, the big banks) routinely file full submissions far above the response
byte cap. The adapter must read those filings from their primary document (plus EX-99 exhibits
for 8-K/6-K), and a filing that still fails must only cost that filing, never the company's
other filings or its XBRL actuals.
"""
from datetime import datetime
import json
import unittest
from unittest.mock import Mock

from tests.research_foundation_fixtures import database, START, ENTRY
from tests.test_company_evidence import ACCESSION, COMPANY, guidance
from services.discovery.company_evidence_policy import CompanyEvidencePolicy
from services.discovery.company_evidence_service import collect_company
from Webscraping.company_evidence.sec import SECAdapter
from Webscraping.company_evidence.transport import ProviderUnavailable

OTHER_ACCESSION = '0001045810-26-000002'
ARCHIVE = 'https://www.sec.gov/Archives/edgar/data/1045810'


def folder(accession):
    return f"{ARCHIVE}/{accession.replace('-', '')}"


def full_submission(accession):
    return f'{folder(accession)}/{accession}.txt'


class FakeSEC:
    """Serves SEC URLs from a table; records every URL requested."""

    def __init__(self, filings, routes):
        self.filings, self.routes, self.requested = filings, routes, []

    def get(self, url, **kwargs):
        self.requested.append(url)
        if '/submissions/' in url:
            recent = {key: [filing.get(key) for filing in self.filings]
                      for key in ('form', 'accessionNumber', 'filingDate', 'acceptanceDateTime', 'reportDate',
                                  'size', 'primaryDocument')}
            return self.response(url, {'cik': 1045810, 'filings': {'recent': recent}})
        if '/companyfacts/' in url:
            return self.response(url, {'cik': 1045810, 'facts': {'us-gaap': {'Revenues': {'units': {'USD': [
                {'start': '2026-04-01', 'end': '2026-06-30', 'val': 100, 'accn': filing['accessionNumber'],
                 'filed': filing['filingDate']} for filing in self.filings]}}}}})
        route = self.routes.get(url)
        if route is None:
            raise AssertionError(f'unexpected SEC request: {url}')
        if isinstance(route, Exception):
            raise route
        return self.response(url, route)

    @staticmethod
    def response(url, body):
        return {'url': url, 'body': body if isinstance(body, str) else json.dumps(body),
                'observed_at': ENTRY, 'response_id': 'response:' + url}


def filing(accession, form, *, size=None, primary=None):
    return {'form': form, 'accessionNumber': accession, 'filingDate': '2026-09-01',
            'acceptanceDateTime': START, 'reportDate': '2026-06-30', 'size': size, 'primaryDocument': primary}


def sgml(form, body):
    return (f'CENTRAL INDEX KEY: 0001045810\n<DOCUMENT><TYPE>{form}\n<TEXT><p>{body}</p></TEXT></DOCUMENT>')


def index(*items):
    return {'directory': {'item': [{'name': name, 'size': str(size), 'type': 'text.gif'} for name, size in items]}}


class OversizeFilingTests(unittest.TestCase):
    def setUp(self):
        self.policy = CompanyEvidencePolicy()
        self.cap = self.policy.max_response_bytes

    def collect(self, filings, routes):
        sec = FakeSEC(filings, routes)
        result = SECAdapter(sec, self.policy).collect(COMPANY, {})
        return sec, result

    def filings_of(self, result, document_type):
        return [document for document in result.documents if document.document_type == document_type]

    def test_oversize_8k_reads_primary_document_and_ex99_instead_of_full_submission(self):
        release = guidance().text
        routes = {
            f'{folder(ACCESSION)}/nvda-8k.htm': '<p>Results furnished in exhibit 99.1.</p>',
            f'{folder(ACCESSION)}/index.json': index(('nvda-8k.htm', 4000), ('ex99-1.htm', 9000),
                                                     ('R1.htm', 300), ('Financial_Report.xlsx', 80000)),
            f'{folder(ACCESSION)}/ex99-1.htm': f'<p>{release}</p>',
        }
        sec, result = self.collect([filing(ACCESSION, '8-K', size=self.cap + 1, primary='nvda-8k.htm')], routes)

        self.assertNotIn(full_submission(ACCESSION), sec.requested)
        [document] = self.filings_of(result, 'regulatory_filing')
        self.assertIn('Results furnished in exhibit 99.1.', document.text)
        self.assertIn(release, document.text)
        self.assertTrue(document.facts, 'guidance in the EX-99 release is still extracted')
        self.assertEqual(document.metadata['source_scope'], 'primary_document')
        self.assertNotIn(f'{folder(ACCESSION)}/R1.htm', sec.requested)

    def test_oversize_exhibit_is_skipped(self):
        routes = {
            f'{folder(ACCESSION)}/nvda-8k.htm': '<p>Cover page.</p>',
            f'{folder(ACCESSION)}/index.json': index(('nvda-8k.htm', 4000), ('ex99-1.htm', self.cap + 1)),
        }
        sec, result = self.collect([filing(ACCESSION, '8-K', size=self.cap + 1, primary='nvda-8k.htm')], routes)
        self.assertNotIn(f'{folder(ACCESSION)}/ex99-1.htm', sec.requested)
        self.assertEqual(len(self.filings_of(result, 'regulatory_filing')), 1)

    def test_oversize_10q_reads_primary_document_without_looking_for_exhibits(self):
        routes = {f'{folder(ACCESSION)}/nvda-10q.htm': '<p>Quarterly report.</p>'}
        sec, result = self.collect([filing(ACCESSION, '10-Q', size=self.cap * 5, primary='nvda-10q.htm')], routes)
        self.assertNotIn(f'{folder(ACCESSION)}/index.json', sec.requested)
        self.assertNotIn(full_submission(ACCESSION), sec.requested)
        [document] = self.filings_of(result, 'regulatory_filing')
        self.assertIn('Quarterly report.', document.text)

    def test_filing_under_the_cap_still_reads_the_full_submission(self):
        routes = {full_submission(ACCESSION): sgml('10-Q', 'Quarterly report.')}
        sec, result = self.collect([filing(ACCESSION, '10-Q', size=self.cap - 1, primary='nvda-10q.htm')], routes)
        self.assertIn(full_submission(ACCESSION), sec.requested)
        self.assertNotIn(f'{folder(ACCESSION)}/nvda-10q.htm', sec.requested)
        self.assertNotEqual(self.filings_of(result, 'regulatory_filing')[0].metadata.get('source_scope'),
                            'primary_document')

    def test_unknown_size_reads_the_full_submission(self):
        routes = {full_submission(ACCESSION): sgml('10-Q', 'Quarterly report.')}
        sec, _ = self.collect([filing(ACCESSION, '10-Q')], routes)
        self.assertIn(full_submission(ACCESSION), sec.requested)


class FailingFilingTests(unittest.TestCase):
    def setUp(self):
        self.policy = CompanyEvidencePolicy()

    def routes(self):
        return {full_submission(ACCESSION): ProviderUnavailable('Evidence response exceeds byte limit'),
                full_submission(OTHER_ACCESSION): sgml('8-K', 'Other filing text.')}

    def filings(self):
        # Newest first, as SEC lists them.
        return [filing(ACCESSION, '10-Q'), filing(OTHER_ACCESSION, '8-K')]

    def test_failing_filing_keeps_other_filings_and_its_own_xbrl_actuals(self):
        result = SECAdapter(FakeSEC(self.filings(), self.routes()), self.policy).collect(COMPANY, {})

        texts = [document for document in result.documents if document.document_type == 'regulatory_filing']
        self.assertEqual([document.metadata['accession'] for document in texts], [OTHER_ACCESSION])
        xbrl = {document.metadata['accession'] for document in result.documents
                if document.document_type == 'xbrl_facts'}
        self.assertEqual(xbrl, {ACCESSION, OTHER_ACCESSION})
        self.assertTrue(any(gap.startswith('sec_filing_unavailable:10-Q:') for gap in result.gaps), result.gaps)

    def test_company_with_one_failing_filing_still_gets_a_package(self):
        db = database(self)
        adapter = SECAdapter(FakeSEC(self.filings(), self.routes()), self.policy)
        result = collect_company(COMPANY, [adapter], self.policy, db_path=db,
                                 clock=lambda: datetime.fromisoformat(ENTRY))
        self.assertIsNotNone(result['package_id'])
        self.assertEqual(result['providers']['sec'], 'collected')


if __name__ == '__main__':
    unittest.main()
