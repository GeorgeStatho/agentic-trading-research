"""EDGAR's market-wide feed of the latest filings, newest first."""
from datetime import datetime, timezone
import html
import re
from xml.etree import ElementTree

ATOM = '{http://www.w3.org/2005/Atom}'
CURRENT_URL = ('https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}&company=&dateb='
               '&owner=include&start={start}&count={count}&output=atom')
PAGE_SIZE = 100
_TITLE = re.compile(r'^(?P<form>\S+) - .* \((?P<cik>\d{10})\) \((?P<role>[^)]+)\)$')
_ACCESSION = re.compile(r'accession-number=(\d{10}-\d{2}-\d{6})')
_ITEM = re.compile(r'Item (\d+\.\d+)')


def parse_current_filings(atom_text):
    """One record per filing; the feed repeats a filing for each subject company, so only filer entries count."""
    return _parse_page(atom_text)[0]


def _parse_page(atom_text):
    """The page's filings and its raw entry count, which alone tells whether more pages follow."""
    filings, seen = [], set()
    entries = list(ElementTree.fromstring(atom_text).iter(ATOM + 'entry'))
    for entry in entries:
        title = _TITLE.match((entry.findtext(ATOM + 'title') or '').strip())
        accession = _ACCESSION.search(entry.findtext(ATOM + 'id') or '')
        if not title or not accession or title['role'] != 'Filer' or accession[1] in seen:
            continue
        seen.add(accession[1])
        filed = datetime.fromisoformat(entry.findtext(ATOM + 'updated').strip())
        summary = html.unescape(entry.findtext(ATOM + 'summary') or '')
        filings.append({'cik': title['cik'], 'form': title['form'], 'accession': accession[1],
                        'filed_at': filed.astimezone(timezone.utc).isoformat(),
                        'items': list(dict.fromkeys(_ITEM.findall(summary)))})
    return filings, len(entries)


class SECCurrentFilings:
    """Pages back through each form's feed until it reaches filings already seen."""

    def __init__(self, http, *, forms=('8-K', '6-K'), max_pages=4):
        self.http, self.forms, self.max_pages = http, forms, max_pages

    def __call__(self, *, since=None):
        cutoff = datetime.fromisoformat(since) if since else None
        filings = []
        for form in self.forms:
            for page in range(self.max_pages):
                url = CURRENT_URL.format(form=form, start=page * PAGE_SIZE, count=PAGE_SIZE)
                batch, entry_count = _parse_page(self.http.get(url, ttl_seconds=300)['body'])
                filings.extend(batch)
                if entry_count < PAGE_SIZE or (cutoff and batch and datetime.fromisoformat(batch[-1]['filed_at']) <= cutoff):
                    break
        return filings
