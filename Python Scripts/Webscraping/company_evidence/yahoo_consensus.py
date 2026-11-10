"""Analyst consensus and the next earnings date from Yahoo's unofficial calendar; free, unlicensed, best effort."""
from datetime import date, datetime
import json
import math

from research_foundation_contracts import timestamp
from .models import CollectionResult, SourceDocument
from services.core.time_utils import utc_now


FACT_VERSION = 'yahoo-calendar-consensus-v1'
METRICS = (('eps_diluted', 'Earnings', 'USD/shares'), ('revenue', 'Revenue', 'USD'))


def _dump(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _earnings_date(value):
    # Yahoo gives a list of one or two candidate dates; the first is the expected report date.
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


class YahooConsensusAdapter:
    name = 'yahoo_consensus'

    def __init__(self, ticker_factory=None, clock=utc_now):
        self.ticker_factory, self.clock = ticker_factory, clock

    def collect(self, company, cursor):
        symbol = company['symbol']
        try:
            factory = self.ticker_factory
            if factory is None:
                import yfinance
                factory = yfinance.Ticker
            ticker = factory(symbol.replace('.', '-'))
            calendar = ticker.calendar
            if not isinstance(calendar, dict):
                calendar = {}
        except Exception:
            ticker, calendar = None, {}
        # The calendar carries no currency, so estimates are labelled USD only when Yahoo says so (SK hynix's
        # won were once stored as USD). A failed lookup falls back to the currency seen on an earlier refresh.
        currency = cursor.get('financial_currency')
        if ticker is not None:
            try:
                currency = (ticker.info or {}).get('financialCurrency') or currency
            except Exception:
                pass
            cursor = {**cursor, 'financial_currency': currency} if currency else cursor
        # A known non-USD reporter is named even when Yahoo is down, so its earlier captures are dropped.
        if currency and currency != 'USD':
            return CollectionResult((), cursor, ('consensus_currency_not_usd',))
        if ticker is not None and not currency:
            return CollectionResult((), cursor, ('consensus_currency_unknown',))
        document = self._document(company, calendar)
        if document is None:
            return CollectionResult((), cursor, ('consensus_provider_unavailable',))
        return CollectionResult((document,), cursor)

    def _document(self, company, calendar):
        earnings = _earnings_date(calendar.get('Earnings Date'))
        earnings_label = earnings.isoformat() if earnings else 'unknown'
        period = f'next_report:{earnings_label}'
        # Each metric is its own sub-object so a fact's source slice carries its period and currency.
        sections = {}
        for metric, label, _ in METRICS:
            average = _number(calendar.get(f'{label} Average'))
            if average is None:
                continue
            high, low = _number(calendar.get(f'{label} High')), _number(calendar.get(f'{label} Low'))
            sections[metric] = {'average': average, 'currency': 'USD', 'fiscal_period': period,
                                **({'high': high} if high is not None and high >= average else {}),
                                **({'low': low} if low is not None and low <= average else {})}
        if not sections and earnings is None:
            return None
        body = {'Earnings Date': [earnings.isoformat()]} if earnings else {}
        text = _dump({**body, **sections})
        facts = []
        for metric, _, unit in METRICS:
            if metric not in sections:
                continue
            section = sections[metric]
            serialized = _dump(section)
            start = text.index(serialized)
            facts.append({'metric': metric, 'kind': 'consensus', 'value': section['average'],
                          **({'range_high': section['high']} if 'high' in section else {}),
                          'unit': unit, 'currency': 'USD', 'scale': 1, 'fiscal_period': period,
                          'period_type': 'quarter', 'accounting_basis': 'analyst_consensus',
                          'source_start': start,
                          'source_end': start + len(serialized),
                          'extraction_version': FACT_VERSION})
        now = timestamp(self.clock().isoformat())
        symbol = company['symbol']
        events = ({'name': 'Earnings release', 'scheduled_at': earnings.isoformat(), 'precision': 'date',
                   'confirmed': False, 'status': 'yahoo_calendar_estimate', 'uncertainty': 'time_not_supplied'},) if earnings else ()
        return SourceDocument(self.name, f'https://finance.yahoo.com/quote/{symbol}/analysis', 'Yahoo Finance',
                              'consensus', text, now, now, f'{self.name}:{symbol}:{earnings_label}',
                              {'cik': company.get('cik'), 'publication_precision': 'timestamp',
                               'source': 'yfinance.Ticker.calendar'}, tuple(facts), events)
