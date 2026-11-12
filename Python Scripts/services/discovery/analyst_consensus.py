"""Analyst consensus: Yahoo's estimate for a company's next report, and what it may be compared with.

An estimate is labelled `next_report:<D>` with D the New York date of the earnings report it is for. It is
stale once captured on or after D, matches only the quarter reported on D, and is collected only in USD.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from services.discovery.company_evidence_extraction import period_end


NEXT_REPORT_PREFIX = 'next_report:'
NEW_YORK = ZoneInfo('America/New_York')
# Reports land 2-8 weeks after quarter end; the previous quarter ends about 120 days before.
REPORTED_QUARTER_MAX_DAYS = 95
# Analyst revenue and reported revenue measure the same thing; analyst EPS is usually adjusted.
BASIS_INDEPENDENT_METRICS = frozenset({'revenue'})
# Collection gaps. A known non-USD reporter keeps none of its earlier captures; an unknown currency keeps them.
CURRENCY_NOT_USD = 'consensus_currency_not_usd'
CURRENCY_UNKNOWN = 'consensus_currency_unknown'


def report_label(day):
    """The fiscal_period of an estimate for the report on `day` (None when Yahoo gives no date)."""
    return f"{NEXT_REPORT_PREFIX}{day.isoformat() if day else 'unknown'}"


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


def retained_consensus(consensus_documents, facts, at):
    """(kept ids, newest id): the newest estimate for a report not yet past, plus the last one captured
    for the most recent report already due by `at`. The newest id is None when there is none.

    Yahoo rolls on to the next earnings date once a company reports, so without the second document
    the estimate the reported quarter is judged against would be gone. A capture still labelled with a
    report date it was taken after is next quarter's estimate under the wrong label, so it is neither
    (see estimated_report). `consensus_documents` is in publication order; older captures are dropped.
    """
    reports, stale = {}, set()
    for fact in facts:
        estimated = estimated_report(fact)
        if estimated is not None:
            reports.setdefault(fact['document_id'], estimated)
        elif report_date(fact) is not None:
            stale.add(fact['document_id'])
    current = [doc for doc in consensus_documents if doc['document_id'] not in stale]
    # Report dates are New York dates, so the package time is compared as one too.
    cutoff = market_date(at)
    # The newest estimate is for a report not yet past; one only for a passed report is a baseline.
    upcoming = [doc['document_id'] for doc in current
                if reports.get(doc['document_id']) is None or reports[doc['document_id']] >= cutoff]
    newest = upcoming[-1] if upcoming else None
    kept = {newest} if newest else set()
    due = [doc for doc in current if reports.get(doc['document_id']) and reports[doc['document_id']] <= cutoff]
    if due:
        last_report = max(reports[doc['document_id']] for doc in due)
        kept.add([doc for doc in due if reports[doc['document_id']] == last_report][-1]['document_id'])
    return kept, newest
