"""Standard entity-wide US-GAAP facts. Never infer a quarter from the filing's FP label."""
from datetime import date

from market_db_modules.foundation_store import canonical


EXTRACTION_VERSION = 'sec-standard-facts-v2'
# One revenue figure per filing and period, the first of these the filing reports: total revenue before a
# contract-revenue line, which for banks, insurers and energy companies leaves out part of the top line.
REVENUE_TAGS = ('Revenues', 'RevenuesNetOfInterestExpense', 'SalesRevenueNet',
                'RevenueFromContractWithCustomerExcludingAssessedTax',
                'RevenueFromContractWithCustomerIncludingAssessedTax')
CONTRACT_REVENUE_TAGS = ('RevenueFromContractWithCustomerExcludingAssessedTax',
                         'RevenueFromContractWithCustomerIncludingAssessedTax')
# Read only to recognise a bank's fee income; never stored as a fact.
NET_INTEREST_INCOME_TAG = 'InterestIncomeExpenseNet'
TAGS = {
    **{tag: 'revenue' for tag in REVENUE_TAGS},
    'NetIncomeLoss': 'net_income', 'EarningsPerShareDiluted': 'eps_diluted',
    'GrossProfit': 'gross_profit', 'OperatingIncomeLoss': 'operating_income',
    'NetCashProvidedByUsedInOperatingActivities': 'operating_cash_flow',
    'CashAndCashEquivalentsAtCarryingValue': 'cash',
    'LongTermDebtCurrent': 'debt_current', 'LongTermDebtNoncurrent': 'debt_noncurrent',
    'LongTermDebt': 'long_term_debt', 'ShortTermBorrowings': 'short_term_borrowings',
}


def fiscal_period(item):
    end = date.fromisoformat(item['end'])
    if not item.get('start'):
        return end.isoformat(), 'instant'
    start = date.fromisoformat(item['start'])
    days = (end - start).days + 1
    if days <= 0:
        raise ValueError('Invalid financial period')
    period_type = 'quarter' if 60 <= days <= 120 else 'annual' if 330 <= days <= 400 else 'duration'
    return f'{start.isoformat()}/{end.isoformat()}', period_type


def company_fact_rows(payload, cik, accessions):
    if str(payload.get('cik', '')).zfill(10) != str(cik).zfill(10):
        raise ValueError('SEC companyfacts returned a different issuer')
    def items(tag, metric):
        units = payload.get('facts', {}).get('us-gaap', {}).get(tag, {}).get('units', {})
        for unit, values in units.items():
            if unit != ('USD/shares' if metric == 'eps_diluted' else 'USD'):
                continue
            for item in values:
                if item.get('accn') in accessions:
                    period, period_type = fiscal_period(item)
                    yield {'tag': tag, 'metric': metric, 'unit': unit, 'fiscal_period': period,
                           'period_type': period_type, 'item': item}

    net_interest = {(row['item']['accn'], row['fiscal_period']): row['item']['val']
                    for row in items(NET_INTEREST_INCOME_TAG, 'net_interest_income')}
    rows, revenue_seen = [], set()
    for tag, metric in TAGS.items():
        for row in items(tag, metric):
            key = (row['item']['accn'], row['fiscal_period'])
            if metric == 'revenue':
                # A contract-revenue line below net interest income is a bank's fee income, not its revenue.
                if key in revenue_seen or (tag in CONTRACT_REVENUE_TAGS and key in net_interest
                                           and row['item']['val'] < net_interest[key]):
                    continue
                revenue_seen.add(key)
            rows.append(row)
    return sorted(rows, key=canonical)


def normalize_company_facts(payload, cik, accessions):
    """An auditable JSON-line per original fact, retaining exact dates, tag and accession."""
    lines, facts, offset = [], [], 0
    for row in company_fact_rows(payload, cik, accessions):
        line = canonical({'cik': str(cik).zfill(10), 'taxonomy': 'us-gaap', **row})
        facts.append({'metric': row['metric'], 'kind': 'actual', 'value': row['item']['val'],
                      'unit': row['unit'], 'currency': 'USD', 'scale': 1,
                      'fiscal_period': row['fiscal_period'], 'period_type': row['period_type'],
                      'accounting_basis': 'US-GAAP', 'source_start': offset, 'source_end': offset + len(line),
                      'extraction_version': EXTRACTION_VERSION})
        lines.append(line)
        offset += len(line) + 1
    return '\n'.join(lines), tuple(facts)
