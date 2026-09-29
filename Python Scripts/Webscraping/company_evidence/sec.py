"""Public SEC submissions, issuer-furnished releases and standard XBRL actuals."""
import json
import re

from research_foundation_contracts import timestamp
from .models import CollectionResult, SourceDocument
from .releases import guidance_facts, release_text
from .sec_facts import normalize_company_facts


TICKERS_URL = 'https://www.sec.gov/files/company_tickers.json'
FORMS = {'10-K', '10-K/A', '10-Q', '10-Q/A', '8-K', '8-K/A', '20-F', '20-F/A', '6-K', '6-K/A'}


class SECAdapter:
    name = 'sec'

    def __init__(self, http, policy):
        self.http, self.policy = http, policy

    def issuer_map(self):
        response = self.http.get(TICKERS_URL, ttl_seconds=86400)
        records = json.loads(response['body']).values()
        result = {}
        for row in records:
            symbol = row['ticker'].upper().replace('.', '-')
            cik = str(row['cik_str']).zfill(10)
            if symbol in result and result[symbol] != cik:
                raise ValueError('Ambiguous SEC ticker mapping')
            result[symbol] = cik
        return result

    def collect(self, company, cursor):
        cik = company['cik']
        url = f'https://data.sec.gov/submissions/CIK{cik}.json'
        response = self.http.get(url, ttl_seconds=self.policy.refresh_seconds)
        payload = json.loads(response['body'])
        if str(payload.get('cik', '')).zfill(10) != cik:
            raise ValueError('SEC submissions returned a different issuer')
        recent = payload.get('filings', {}).get('recent', {})
        filings = self._filings(recent)
        facts_url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'
        gaps = []
        facts_response = None
        try:
            facts_response = self.http.get(facts_url, ttl_seconds=self.policy.refresh_seconds)
        except Exception as exc:
            # Filing text is still useful without standard XBRL coverage (e.g. foreign filers).
            gaps.append(f'sec_companyfacts_unavailable:{type(exc).__name__}')
        documents = []
        for filing in reversed(filings):
            documents.extend(self._filing_documents(company, filing, facts_response))
        return CollectionResult(tuple(documents), {'accessions': [f['accessionNumber'] for f in filings],
                                                   'submissions_response_id': response['response_id']}, tuple(gaps))

    def _filings(self, recent):
        rows = []
        for index, form in enumerate(recent.get('form', [])):
            if form not in FORMS:
                continue
            row = {key: values[index] for key, values in recent.items() if index < len(values)}
            accession = row.get('accessionNumber', '')
            if not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession):
                raise ValueError('Invalid filing accession')
            rows.append(row)
        return rows[:self.policy.filings_per_company]

    def _filing_documents(self, company, filing, facts_response):
        accession = filing['accessionNumber']
        url = f"https://www.sec.gov/Archives/edgar/data/{int(company['cik'])}/{accession.replace('-', '')}/{accession}.txt"
        response = self.http.get(url, ttl_seconds=self.policy.refresh_seconds)
        accepted = filing.get('acceptanceDateTime')
        publication = timestamp(accepted) if accepted else response['observed_at']
        metadata = {'cik': company['cik'], 'accession': accession, 'form': filing['form'],
                    'filing_date': filing.get('filingDate'), 'report_date': filing.get('reportDate'),
                    'publication_precision': 'timestamp' if accepted else 'unknown',
                    'raw_response_id': response['response_id']}
        text = self._read_filing(response['body'])
        # Complete submissions can contain other registrants. The SEC header must agree.
        header_cik = re.search(r'CENTRAL INDEX KEY:\s*(\d+)', response['body'])
        if header_cik and header_cik[1].zfill(10) != company['cik']:
            raise ValueError('Filing header issuer mismatch')
        guidance = guidance_facts(text)
        documents = [SourceDocument(self.name, url, company['name'], 'regulatory_filing', text,
                                    publication, response['observed_at'], accession, metadata, guidance,
                                    gaps=() if guidance else ('guidance_not_extracted_from_filing',))]
        if facts_response:
            normalized, facts = normalize_company_facts(json.loads(facts_response['body']), company['cik'], {accession})
            if facts:
                documents.append(SourceDocument(self.name, facts_response['url'], company['name'], 'xbrl_facts',
                                                normalized, publication, facts_response['observed_at'], accession,
                                                {**metadata, 'raw_response_id': facts_response['response_id']}, facts))
        return documents

    def _read_filing(self, raw):
        blocks = re.findall(r'<DOCUMENT>(.*?)</DOCUMENT>', raw, flags=re.S | re.I)
        texts = []
        for block in blocks:
            kind = re.search(r'<TYPE>([^\r\n<]+)', block, re.I)
            if not kind or (kind[1].strip() not in FORMS and not kind[1].strip().startswith('EX-99')):
                continue
            content = re.search(r'<TEXT>(.*?)</TEXT>', block, re.S | re.I)
            if content:
                texts.append(release_text(content[1]))
        return '\n'.join(texts) if texts else release_text(raw)
