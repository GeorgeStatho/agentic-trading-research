"""Public SEC submissions, issuer-furnished releases and standard XBRL actuals."""
from hashlib import sha256
import json
import re

from research_foundation_contracts import timestamp
from .models import CollectionResult, SourceDocument
from .releases import guidance_facts, release_text
from .sec_facts import normalize_company_facts


TICKERS_URL = 'https://www.sec.gov/files/company_tickers.json'
FORMS = {'10-K', '10-K/A', '10-Q', '10-Q/A', '8-K', '8-K/A', '20-F', '20-F/A', '6-K', '6-K/A'}
# Current reports whose substance (the earnings release) is furnished as an EX-99 exhibit.
EXHIBIT_FORMS = {'8-K', '8-K/A', '6-K', '6-K/A'}
MAX_EXHIBITS = 3
_ARCHIVE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
_EX99 = re.compile(r'ex[-_]?99', re.I)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _size(filing):
    return _int(filing.get('size'))
# Attachment types that are images or archives; _read_filing never reads them.
BINARY_TYPES = {'GRAPHIC', 'ZIP', 'PDF', 'EXCEL', 'XLSX'}
_IN_DOCUMENT = r'(?:(?!</DOCUMENT>).)*?'
_DOCUMENT = re.compile(rf'(<DOCUMENT>{_IN_DOCUMENT}<TYPE>([^\r\n<]+){_IN_DOCUMENT}<TEXT>)({_IN_DOCUMENT})(</TEXT>)',
                       re.S | re.I)


def strip_binary_attachments(raw):
    """Replace the content of binary attachment documents with a note of their size and hash."""
    def replace(match):
        content = match[3]
        uuencoded = re.match(r'\s*begin [0-7]{3} ', content) is not None
        if match[2].strip().upper() not in BINARY_TYPES and not uuencoded:
            return match[0]
        note = f'[binary attachment removed: {len(content)} chars, sha256={sha256(content.encode()).hexdigest()}]'
        return f'{match[1]}\n{note}\n{match[4]}'
    return _DOCUMENT.sub(replace, raw)


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
            # A filing that cannot be read costs only its own text: the company's other filings
            # and this filing's XBRL actuals (from companyfacts, not the submission) are kept.
            text_document = None
            try:
                text_document = self._filing_text_document(company, filing)
                documents.append(text_document)
            except Exception as exc:
                size = _size(filing)
                gaps.append(f"sec_filing_unavailable:{filing['form']}:{type(exc).__name__}"
                            + (f':{size}b' if size else ''))
            if facts_response:
                facts_document = self._facts_document(company, filing, facts_response, text_document)
                if facts_document:
                    documents.append(facts_document)
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

    @staticmethod
    def _folder(company, filing):
        return (f"https://www.sec.gov/Archives/edgar/data/{int(company['cik'])}/"
                f"{filing['accessionNumber'].replace('-', '')}")

    @staticmethod
    def _metadata(company, filing):
        accepted = filing.get('acceptanceDateTime')
        return {'cik': company['cik'], 'accession': filing['accessionNumber'], 'form': filing['form'],
                'filing_date': filing.get('filingDate'), 'report_date': filing.get('reportDate'),
                'publication_precision': 'timestamp' if accepted else 'unknown'}

    @staticmethod
    def _publication(filing, observed_at):
        accepted = filing.get('acceptanceDateTime')
        return timestamp(accepted) if accepted else observed_at

    def _filing_text_document(self, company, filing):
        size, primary = _size(filing), filing.get('primaryDocument')
        if size and size > self.policy.max_response_bytes and primary:
            # The full submission is over the response cap (large filers' XBRL exhibits);
            # the primary document carries the filing's own text.
            return self._primary_document(company, filing, primary)
        accession = filing['accessionNumber']
        url = f'{self._folder(company, filing)}/{accession}.txt'
        response = self.http.get(url, ttl_seconds=self.policy.refresh_seconds, compact=self.compact_filing)
        text = self._read_filing(response['body'])
        # Complete submissions can contain other registrants. The SEC header must agree.
        header_cik = re.search(r'CENTRAL INDEX KEY:\s*(\d+)', response['body'])
        if header_cik and header_cik[1].zfill(10) != company['cik']:
            raise ValueError('Filing header issuer mismatch')
        metadata = {**self._metadata(company, filing), 'raw_response_id': response['response_id']}
        return self._text_document(company, filing, url, text, response['observed_at'], metadata)

    def _primary_document(self, company, filing, primary):
        if not _ARCHIVE_NAME.fullmatch(primary):
            raise ValueError('Invalid SEC primary document name')
        folder = self._folder(company, filing)
        url = f'{folder}/{primary}'
        response = self.http.get(url, ttl_seconds=self.policy.refresh_seconds)
        texts, exhibit_ids = [release_text(response['body'])], []
        if filing['form'] in EXHIBIT_FORMS:
            for name in self._exhibit_names(folder, primary):
                try:
                    exhibit = self.http.get(f'{folder}/{name}', ttl_seconds=self.policy.refresh_seconds)
                except Exception:
                    continue
                texts.append(release_text(exhibit['body']))
                exhibit_ids.append(exhibit['response_id'])
        metadata = {**self._metadata(company, filing), 'raw_response_id': response['response_id'],
                    'source_scope': 'primary_document', 'exhibit_response_ids': exhibit_ids}
        text = '\n'.join(part for part in texts if part)
        return self._text_document(company, filing, url, text, response['observed_at'], metadata)

    def _exhibit_names(self, folder, primary):
        listing = self.http.get(f'{folder}/index.json', ttl_seconds=self.policy.refresh_seconds)
        names = []
        for item in json.loads(listing['body']).get('directory', {}).get('item', []):
            name = str(item.get('name', ''))
            size = _int(item.get('size'))
            if (name != primary and _ARCHIVE_NAME.fullmatch(name) and _EX99.search(name)
                    and name.lower().endswith(('.htm', '.html', '.txt'))
                    and not (size and size > self.policy.max_response_bytes)):
                names.append(name)
        return names[:MAX_EXHIBITS]

    def _text_document(self, company, filing, url, text, observed_at, metadata):
        guidance = guidance_facts(text)
        return SourceDocument(self.name, url, company['name'], 'regulatory_filing', text,
                              self._publication(filing, observed_at), observed_at, filing['accessionNumber'],
                              metadata, guidance, gaps=() if guidance else ('guidance_not_extracted_from_filing',))

    def _facts_document(self, company, filing, facts_response, text_document):
        accession = filing['accessionNumber']
        normalized, facts = normalize_company_facts(json.loads(facts_response['body']), company['cik'], {accession})
        if not facts:
            return None
        metadata = {**(text_document.metadata if text_document else self._metadata(company, filing)),
                    'raw_response_id': facts_response['response_id']}
        publication = (text_document.published_at if text_document
                       else self._publication(filing, facts_response['observed_at']))
        return SourceDocument(self.name, facts_response['url'], company['name'], 'xbrl_facts', normalized,
                              publication, facts_response['observed_at'], accession, metadata, facts)

    def compact_filing(self, raw):
        """The submission without binary attachments, when that leaves everything read from it unchanged."""
        stripped = strip_binary_attachments(raw)
        if stripped == raw or self._read_filing(stripped) != self._read_filing(raw):
            return raw
        return stripped

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
