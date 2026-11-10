"""One oversize or failing SEC filing must not wipe out a company's evidence.

Large filers (ADP, MSFT, the big banks) routinely file full submissions far above the response
byte cap. The adapter must read those filings from their primary document (plus EX-99 exhibits
for 8-K/6-K), and a filing that still fails must only cost that filing, never the company's
other filings or its XBRL actuals.
"""
from datetime import datetime
import json
import unittest

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
                                  'size', 'primaryDocument', 'items')}
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


def index_url(accession):
    return f'{folder(accession)}/{accession}-index.htm'


def _row(sequence, description, href, name, kind, size, inline=False):
    link = f'<a href="{href}">{name}</a>' + (' &nbsp;&nbsp;<span style="color: green">iXBRL</span>' if inline else '')
    return (f'<tr>\n<td scope="row">{sequence}</td>\n<td scope="row">{description}</td>\n'
            f'<td scope="row">{link}</td>\n<td scope="row">{kind}</td>\n<td scope="row">{size}</td>\n</tr>\n')


def index_page(accession, documents, data_files=()):
    """An EDGAR filing index page: the Document Format Files table, then the Data Files table.

    `documents` rows are (name, type, size) or (name, type, size, href); the primary document's link
    goes through the inline-XBRL viewer as EDGAR renders it.
    """
    path = f"/Archives/edgar/data/1045810/{accession.replace('-', '')}"
    header = ('<tr>\n<th scope="col"><acronym title="Sequence Number">Seq</acronym></th>\n'
              '<th scope="col">Description</th>\n<th scope="col">Document</th>\n'
              '<th scope="col">Type</th>\n<th scope="col">Size</th>\n</tr>\n')
    rows = []
    for sequence, document in enumerate(documents, 1):
        name, kind, size = document[:3]
        inline = sequence == 1
        href = document[3] if len(document) > 3 else (f'/ix?doc={path}/{name}' if inline else f'{path}/{name}')
        rows.append(_row(sequence, kind, href, name, kind, size, inline))
    rows.append(_row('&nbsp;', 'Complete submission text file', f'{path}/{accession}.txt', f'{accession}.txt',
                     '&nbsp;', 99_000_000))
    data = ''.join(_row(len(documents) + n, 'XBRL TAXONOMY EXTENSION SCHEMA DOCUMENT', f'{path}/{name}', name,
                        kind, 5000) for n, (name, kind) in enumerate(data_files, 1))
    return ('<html><body><div id="formDiv"><div class="formGrouping">'
            '<table class="tableFile" summary="Document Format Files">\n' + header + ''.join(rows) + '</table>\n'
            '<table class="tableFile" summary="Data Files">\n' + header + data + '</table>\n'
            '</div></div></body></html>')


class OversizeFilingTests(unittest.TestCase):
    def setUp(self):
        self.policy = CompanyEvidencePolicy()
        self.cap = self.policy.max_response_bytes

    def collect(self, filings, routes):
        sec = FakeSEC(filings, routes)
        result = SECAdapter(sec, self.policy).collect(COMPANY, {})
        return sec, result

    def collect_8k(self, documents, routes, data_files=()):
        routes = {f'{folder(ACCESSION)}/nvda-8k.htm': '<p>Results furnished in exhibit 99.1.</p>',
                  index_url(ACCESSION): index_page(ACCESSION, documents, data_files), **routes}
        return self.collect([filing(ACCESSION, '8-K', size=self.cap + 1, primary='nvda-8k.htm')], routes)

    def filings_of(self, result, document_type):
        return [document for document in result.documents if document.document_type == document_type]

    def test_oversize_8k_reads_primary_document_and_ex99_instead_of_full_submission(self):
        release = guidance().text
        # Real issuers' release filenames often never say "ex99" (this one is from a stored filing).
        sec, result = self.collect_8k([('nvda-8k.htm', '8-K', 4000), ('a3q26earningspr.htm', 'EX-99.1', 9000)],
                                      {f'{folder(ACCESSION)}/a3q26earningspr.htm': f'<p>{release}</p>'})

        self.assertNotIn(full_submission(ACCESSION), sec.requested)
        [document] = self.filings_of(result, 'regulatory_filing')
        self.assertIn('Results furnished in exhibit 99.1.', document.text)
        self.assertIn(release, document.text)
        self.assertTrue(document.facts, 'guidance in the EX-99 release is still extracted')
        self.assertEqual(document.metadata['source_scope'], 'primary_document')
        self.assertEqual(document.metadata['exhibit_response_ids'],
                         [f'response:{folder(ACCESSION)}/a3q26earningspr.htm'])

    def test_exhibits_are_chosen_by_index_type_not_filename(self):
        exhibit = f'{folder(ACCESSION)}/'
        sec, result = self.collect_8k(
            [('nvda-8k.htm', '8-K', 4000), ('ex99-letter.htm', 'EX-10.1', 3000),
             ('cop-20261105x8kexx991.htm', 'EX-99.1', 9000), ('uberq326earningspressrelea.htm', 'EX-99.2', 8000),
             ('nvda-20261105_g1.jpg', 'GRAPHIC', 2000), ('R1.htm', 'XML', 300)],
            {exhibit + 'cop-20261105x8kexx991.htm': '<p>Third quarter release.</p>',
             exhibit + 'uberq326earningspressrelea.htm': '<p>Supplemental tables.</p>'},
            data_files=[('nvda-20261105.xsd', 'EX-101.SCH'), ('nvda-20261105_lab.xml', 'EX-101.LAB')])

        [document] = self.filings_of(result, 'regulatory_filing')
        self.assertIn('Third quarter release.', document.text)
        self.assertIn('Supplemental tables.', document.text)
        for skipped in ('ex99-letter.htm', 'nvda-20261105_g1.jpg', 'R1.htm', 'nvda-20261105.xsd',
                        'nvda-20261105_lab.xml'):
            self.assertNotIn(exhibit + skipped, sec.requested)
        self.assertNotIn(full_submission(ACCESSION), sec.requested)
        self.assertNotIn(exhibit + 'index.json', sec.requested)

    def test_at_most_three_ex99_exhibits_in_index_order(self):
        names = [f'release{n}.htm' for n in range(1, 5)]
        sec, _ = self.collect_8k([('nvda-8k.htm', '8-K', 4000)] + [(name, f'EX-99.{n}', 1000)
                                                                  for n, name in enumerate(names, 1)],
                                 {f'{folder(ACCESSION)}/{name}': f'<p>{name}</p>' for name in names})
        fetched = [url.rsplit('/', 1)[1] for url in sec.requested if url.rsplit('/', 1)[1] in names]
        self.assertEqual(fetched, names[:3])

    def test_oversize_exhibit_is_skipped(self):
        sec, result = self.collect_8k([('nvda-8k.htm', '8-K', 4000), ('a3q26earningspr.htm', 'EX-99.1', self.cap + 1)],
                                      {})
        self.assertNotIn(f'{folder(ACCESSION)}/a3q26earningspr.htm', sec.requested)
        self.assertEqual(len(self.filings_of(result, 'regulatory_filing')), 1)

    def test_exhibit_linked_outside_the_filing_folder_is_not_fetched(self):
        elsewhere = '/Archives/edgar/data/999999/000099999926000001/release.htm'
        sec, result = self.collect_8k([('nvda-8k.htm', '8-K', 4000), ('release.htm', 'EX-99.1', 1000, elsewhere)],
                                      {})
        self.assertFalse([url for url in sec.requested if url.endswith('/release.htm')])
        self.assertEqual(len(self.filings_of(result, 'regulatory_filing')), 1)

    def test_unreadable_index_or_exhibit_keeps_the_primary_document(self):
        for routes in ({index_url(ACCESSION): ProviderUnavailable('sec HTTP 503')},
                       {f'{folder(ACCESSION)}/a3q26earningspr.htm': ProviderUnavailable('sec HTTP 503')}):
            with self.subTest(failing=next(iter(routes))):
                _, result = self.collect_8k([('nvda-8k.htm', '8-K', 4000), ('a3q26earningspr.htm', 'EX-99.1', 9000)],
                                            routes)
                [document] = self.filings_of(result, 'regulatory_filing')
                self.assertIn('Results furnished in exhibit 99.1.', document.text)
                self.assertEqual(document.metadata['exhibit_response_ids'], [])

    def test_oversize_10q_reads_primary_document_without_looking_for_exhibits(self):
        routes = {f'{folder(ACCESSION)}/nvda-10q.htm': '<p>Quarterly report.</p>'}
        sec, result = self.collect([filing(ACCESSION, '10-Q', size=self.cap * 5, primary='nvda-10q.htm')], routes)
        self.assertNotIn(index_url(ACCESSION), sec.requested)
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


def eight_k_accession(n):
    return f'0001045810-26-00010{n}'


class FilingWindowTests(unittest.TestCase):
    """MSFT and DELL, 2026-11-09: three newer 8-Ks pushed the latest 10-Q, and its actuals, out of the window."""

    def setUp(self):
        self.policy = CompanyEvidencePolicy()

    def accessions_read(self, filings):
        routes = {full_submission(f['accessionNumber']): sgml(f['form'], f"{f['form']} text.") for f in filings}
        result = SECAdapter(FakeSEC(filings, routes), self.policy).collect(COMPANY, {})
        return [(d.document_type, d.metadata['accession']) for d in result.documents]

    def test_the_newest_periodic_report_is_read_behind_newer_8ks(self):
        filings = [filing(eight_k_accession(n), '8-K') for n in range(4)] + [filing(ACCESSION, '10-Q')]
        read = self.accessions_read(filings)
        self.assertIn(('regulatory_filing', ACCESSION), read)
        self.assertIn(('xbrl_facts', ACCESSION), read)
        self.assertEqual({a for kind, a in read if kind == 'regulatory_filing'},
                         {eight_k_accession(0), eight_k_accession(1), eight_k_accession(2), ACCESSION})

    def test_a_periodic_report_already_in_the_window_adds_nothing(self):
        filings = [filing(eight_k_accession(0), '8-K'), filing(ACCESSION, '10-Q'),
                   filing(eight_k_accession(1), '8-K'), filing(eight_k_accession(2), '8-K')]
        read = self.accessions_read(filings)
        self.assertEqual(len([a for kind, a in read if kind == 'regulatory_filing']), 3)


class ReleaseStartTests(unittest.TestCase):
    """An 8-K's own cover text comes first; the strategist is shown the earnings release that follows it."""

    def test_a_primary_document_8k_records_where_its_exhibit_starts(self):
        tests = OversizeFilingTests()
        tests.setUp()
        _, result = tests.collect_8k([('nvda-8k.htm', '8-K', 4000), ('a3q26earningspr.htm', 'EX-99.1', 9000)],
                                     {f'{folder(ACCESSION)}/a3q26earningspr.htm': '<p>Third quarter release.</p>'})
        [document] = tests.filings_of(result, 'regulatory_filing')
        self.assertTrue(document.text[document.metadata['release_start']:].startswith('Third quarter release.'))

    def test_a_full_submission_8k_records_where_its_first_ex99_starts(self):
        body = ('CENTRAL INDEX KEY: 0001045810\n'
                '<DOCUMENT><TYPE>8-K\n<TEXT><p>Item 2.02 Results of Operations.</p></TEXT></DOCUMENT>\n'
                '<DOCUMENT><TYPE>EX-99.1\n<TEXT><p>Fixture reports record revenue.</p></TEXT></DOCUMENT>')
        result = SECAdapter(FakeSEC([filing(ACCESSION, '8-K')], {full_submission(ACCESSION): body}),
                            CompanyEvidencePolicy()).collect(COMPANY, {})
        [document] = [d for d in result.documents if d.document_type == 'regulatory_filing']
        self.assertTrue(document.text[document.metadata['release_start']:].startswith('Fixture reports record revenue.'))

    def test_the_hidden_inline_xbrl_header_is_not_release_text(self):
        # MSFT's 10-28 EX-99.1: 6 MB of hidden ix:header values (P2Y, fasb.org URIs) opened the excerpt.
        from Webscraping.company_evidence.releases import release_text
        html = ('<div style="display:none"><ix:header><ix:hidden><ix:nonNumeric name="us-gaap:UsefulLife">P2Y'
                '</ix:nonNumeric></ix:hidden><ix:resources>http://fasb.org/us-gaap/2025#DerivativeAssets'
                '</ix:resources></ix:header></div><p>Microsoft Cloud revenue increased 26%.</p>')
        self.assertEqual(release_text(html), 'Microsoft Cloud revenue increased 26%.')

    def test_only_an_8k_reporting_results_is_a_release(self):
        # MSFT, 2026-11: the newest 8-Ks were item 5.02 (officers) and 8.01; item 2.02 is results of operations.
        body = ('CENTRAL INDEX KEY: 0001045810\n<DOCUMENT><TYPE>8-K\n<TEXT><p>Cover.</p></TEXT></DOCUMENT>\n'
                '<DOCUMENT><TYPE>EX-99.1\n<TEXT><p>Exhibit text.</p></TEXT></DOCUMENT>')
        for items, expected in (('5.02,9.01', False), ('2.02,9.01', True)):
            with self.subTest(items=items):
                result = SECAdapter(FakeSEC([{**filing(ACCESSION, '8-K'), 'items': items}],
                                            {full_submission(ACCESSION): body}),
                                    CompanyEvidencePolicy()).collect(COMPANY, {})
                [document] = [d for d in result.documents if d.document_type == 'regulatory_filing']
                self.assertEqual('release_start' in document.metadata, expected)

    def test_a_10q_has_no_release_start(self):
        result = SECAdapter(FakeSEC([filing(ACCESSION, '10-Q')], {full_submission(ACCESSION): sgml('10-Q', 'Q.')}),
                            CompanyEvidencePolicy()).collect(COMPANY, {})
        [document] = [d for d in result.documents if d.document_type == 'regulatory_filing']
        self.assertNotIn('release_start', document.metadata)


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
