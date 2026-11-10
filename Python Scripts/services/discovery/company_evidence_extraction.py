"""Validate structured extraction before facts cross the persistence boundary."""
from dataclasses import replace
from datetime import date, datetime
import math
import re
from zoneinfo import ZoneInfo

from market_db_modules.company_evidence import (
    get_company_fact, get_evidence_document, list_company_facts, record_company_fact, record_evidence_document,
)
from market_db_modules.foundation_store import digest
from research_foundation_contracts import CompanyFact, EvidenceDocument


COMPARISON_FIELDS = ('metric', 'fiscal_period', 'period_type', 'accounting_basis', 'unit', 'currency', 'scale')
NEXT_REPORT_PREFIX = 'next_report:'
NEW_YORK = ZoneInfo('America/New_York')
# Reports land 2-8 weeks after quarter end; the previous quarter ends about 120 days before.
REPORTED_QUARTER_MAX_DAYS = 95
# Analyst revenue and reported revenue measure the same thing; analyst EPS is usually adjusted.
BASIS_INDEPENDENT_METRICS = frozenset({'revenue'})


def comparison_key(fact):
    return tuple(fact.get(key) for key in COMPARISON_FIELDS)


def report_date(fact):
    """The earnings date a `next_report:<D>` estimate is for, or None."""
    period = str(fact.get('fiscal_period') or '')
    if not period.startswith(NEXT_REPORT_PREFIX):
        return None
    try:
        return date.fromisoformat(period[len(NEXT_REPORT_PREFIX):])
    except ValueError:
        return None


def estimated_report(fact):
    """report_date(fact), unless the estimate was captured on or after that date (then None).

    Yahoo keeps showing a passed earnings date for days while its estimates have moved on to the next
    quarter, so a capture from D on is next quarter's estimate under D's label. Captures on D itself had
    often rolled already, even before the release, so the last one from before D is the baseline. A fact
    without a capture time is taken at its label.
    """
    reported = report_date(fact)
    captured = str(fact.get('available_at') or '')
    if reported is not None and captured and market_date(captured) >= reported:
        return None
    return reported


def market_date(timestamp):
    # Report dates are exchange dates: a capture at 21:00 ET is still the evening before, whatever UTC says.
    moment = datetime.fromisoformat(timestamp)
    return moment.astimezone(NEW_YORK).date() if moment.tzinfo else moment.date()


def period_end(period):
    parts = str(period or '').split('/')
    try:
        return date.fromisoformat(parts[1]) if len(parts) == 2 else None
    except ValueError:
        return None


def consensus_matches_actual(consensus, actual):
    """True when `consensus` estimated the quarter `actual` reports.

    The estimate for the report on D is for the latest quarter that ended before D, so the actual must
    be quarterly and end within REPORTED_QUARTER_MAX_DAYS before D. That excludes the previous quarter
    and the prior-year comparative columns a 10-Q also carries.
    """
    if (consensus.get('kind') != 'consensus' or actual.get('kind') != 'actual'
            or consensus.get('period_type') != 'quarter' or actual.get('period_type') != 'quarter'):
        return False
    reported, ended = estimated_report(consensus), period_end(actual.get('fiscal_period'))
    if reported is None or ended is None:
        return False
    if any(consensus.get(key) != actual.get(key) for key in ('metric', 'unit', 'currency', 'scale')):
        return False
    if (consensus.get('metric') not in BASIS_INDEPENDENT_METRICS
            and consensus.get('accounting_basis') != actual.get('accounting_basis')):
        return False
    return 0 < (reported - ended).days <= REPORTED_QUARTER_MAX_DAYS


def guidance_midpoint(fact):
    """Midpoint of a single value or a range: the one formula every guidance comparison uses."""
    # A consensus value is already the mean estimate; its range_high is the highest analyst's estimate.
    high = None if fact.get('kind') == 'consensus' else fact.get('range_high')
    return (fact['value'] + (high if high is not None else fact['value'])) / 2


def validate_extracted_fact(proposal, text, company):
    proposal = dict(proposal)
    supplied_company = proposal.pop('company_id', company['id'])
    if supplied_company != company['id']:
        raise ValueError('Extracted fact belongs to another company')
    start, end = proposal.get('source_start'), proposal.get('source_end')
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
        raise ValueError('Invalid extracted source span')
    excerpt = text[start:end]
    numbers = [float(value.replace(',', '')) for value in re.findall(r'-?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][+-]?\d+)?', excerpt)]
    for name in ('value', 'range_high'):
        value = proposal.get(name)
        if value is not None and (isinstance(value, bool) or not math.isfinite(value)
                                  or not any(math.isclose(value, n, rel_tol=1e-12) for n in numbers)):
            raise ValueError('Extracted number is not present in source excerpt')
    if proposal.get('kind') == 'derived':
        raise ValueError('Provider extraction cannot assert derived calculations')
    _validate_context(proposal, excerpt, text)
    return proposal


def _validate_context(proposal, excerpt, text):
    period = proposal.get('fiscal_period', '')
    fiscal = re.fullmatch(r'FY(20\d{2})(?:-(Q[1-4]))?', period)
    if fiscal:
        if fiscal[1] not in excerpt or (fiscal[2] and fiscal[2].lower() not in excerpt.lower()):
            raise ValueError('Extracted fiscal period is not present in source excerpt')
    elif not all(part in excerpt for part in period.split('/')):
        raise ValueError('Extracted period dates are not present in source excerpt')
    basis = proposal.get('accounting_basis')
    compact = re.sub(r'[^a-z]', '', excerpt.lower())
    if basis == 'US-GAAP' and ('gaap' not in compact or 'nongaap' in compact or 'adjusted' in compact):
        raise ValueError('Extracted accounting basis is unsupported or ambiguous')
    if basis == 'adjusted' and not ('adjusted' in compact or 'nongaap' in compact):
        raise ValueError('Adjusted accounting basis is not supported by excerpt')
    if proposal.get('currency') == 'USD' and not re.search(r'\bUSD\b|U\.S\. dollars|US\$', text, re.I):
        raise ValueError('USD currency is not supported by document')
    if proposal.get('unit') == 'percent' and '%' not in excerpt and 'percent' not in excerpt.lower():
        raise ValueError('Percentage unit is not supported by excerpt')


def ingest_source(company, source, at, *, db_path):
    if source.document_type == 'consensus':
        # Re-fetched every refresh: without the fetch times, an unchanged calendar stays one document,
        # so it adds no rows and leaves the package revision (and discovery) alone.
        parts = [company['id'], source.provider, source.url, source.text, source.event_key, source.scheduled_events]
    else:
        # Content, not fetch time: the same filing fetched again is the same document (JPM's 10-Q was stored six times).
        parts = [company['id'], source.provider, source.url, source.text,
                 source.event_key, source.published_at, source.scheduled_events]
    identity = 'document:' + digest(parts)
    try:
        document = get_evidence_document(identity, db_path=db_path)
    except ValueError as exc:
        if not str(exc).startswith('Unknown '):
            raise
        document = _store_document(identity, company, source, at, db_path)
    prior = list_company_facts(company['id'], as_of=at, db_path=db_path)
    identities = []
    for proposal in source.facts:
        fields = validate_extracted_fact(proposal, source.text, company)
        fact_id = 'fact:' + digest([identity, fields])
        try:
            get_company_fact(fact_id, db_path=db_path)
        except ValueError as exc:
            if not str(exc).startswith('Unknown '):
                raise
            fact = CompanyFact(fact_id=fact_id, company_id=company['id'], document_id=identity,
                               available_at=at, **fields)
            predecessor = _predecessor(fields, prior, document, db_path)
            if predecessor:
                fact = replace(fact, supersedes_fact_id=predecessor['fact_id'])
            record_company_fact(fact, db_path=db_path)
        identities.append(fact_id)
    return identity, identities


def _store_document(identity, company, source, at, db_path):
    from contextlib import closing
    from db_common import get_connection
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute('''SELECT record_json FROM evidence_documents
            WHERE provider=? ORDER BY first_observed_at DESC''', (source.provider,)).fetchall()
    import json
    prior = next((d for row in rows if (d := json.loads(row['record_json']))['source_url'] == source.url
                  and d.get('metadata', {}).get('company_id') == company['id']
                  and d.get('metadata', {}).get('event_key') == source.event_key), None)
    document = EvidenceDocument(document_id=identity, provider=source.provider, source_url=source.url,
                                publisher=source.publisher, document_type=source.document_type, text=source.text,
                                published_at=source.published_at, first_observed_at=source.observed_at,
                                ingested_at=at, supersedes_document_id=prior['document_id'] if prior else None,
                                metadata={**source.metadata, 'company_id': company['id'], 'event_key': source.event_key,
                                          'scheduled_events': list(source.scheduled_events), 'gaps': list(source.gaps)})
    record_evidence_document(document, db_path=db_path)
    return get_evidence_document(identity, db_path=db_path)


def _predecessor(fields, prior, document, db_path):
    candidates = [fact for fact in prior if comparison_key(fact) == comparison_key(fields)
                  and fact['kind'] == fields['kind'] and fact['document_id'] != document['document_id']]
    for fact in reversed(candidates):
        old = get_evidence_document(fact['document_id'], db_path=db_path)
        # Two simultaneous conflicting disclosures remain independent, not silently superseded.
        if (old['published_at'] < document['published_at']
                or old['document_id'] == document.get('supersedes_document_id')):
            return fact
    return None
