"""Standard entity-wide US-GAAP facts. Never infer a quarter from the filing's FP label."""
from datetime import date

from market_db_modules.foundation_store import canonical


TAGS = {
    'RevenueFromContractWithCustomerExcludingAssessedTax': 'revenue',
    'Revenues': 'revenue', 'SalesRevenueNet': 'revenue',
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
    rows = []
    for tag, metric in TAGS.items():
        units = payload.get('facts', {}).get('us-gaap', {}).get(tag, {}).get('units', {})
        for unit, values in units.items():
            if unit != ('USD/shares' if metric == 'eps_diluted' else 'USD'):
                continue
            for item in values:
                if item.get('accn') not in accessions:
                    continue
                period, period_type = fiscal_period(item)
                rows.append({'tag': tag, 'metric': metric, 'unit': unit, 'fiscal_period': period,
                             'period_type': period_type, 'item': item})
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
                      'extraction_version': 'sec-standard-facts-v1'})
        lines.append(line)
        offset += len(line) + 1
    return '\n'.join(lines), tuple(facts)
