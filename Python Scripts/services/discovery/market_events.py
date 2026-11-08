"""Market-wide SEC filings add companies outside the static ranking to discovery's interest.

Interest is all this grants: evidence collection and discovery then treat the company like any other,
and nothing here reaches the strategist, manager or broker.
"""
from collections import Counter
from contextlib import closing
from datetime import datetime
import json
import os

from db_common import get_connection
from market_db_modules.bootstrap import initialize_database
from market_db_modules.company_collection import collection_state, save_collection_state
from market_db_modules.company_interest import active_company_ids, register_interest
from services.discovery.company_evidence_policy import CompanyEvidencePolicy
from services.discovery.discovery_policy import DiscoveryPolicy
from Webscraping.company_evidence.sec import SECAdapter
from Webscraping.company_evidence.sec_current import SECCurrentFilings
from Webscraping.company_evidence.transport import EvidenceHTTP
from services.core.time_utils import utc_now

SOURCE = 'market_event'
FEED_STATE = ('sec_current', 'market')
# 8-K items most likely to move the stock: earnings, material agreements, acquisitions, bankruptcy,
# officer changes, Reg FD and other events. Anything else ranks below them.
ITEM_PRIORITY = {'2.02': 3, '1.01': 2, '2.01': 2, '1.03': 2, '5.02': 1, '7.01': 1, '8.01': 1}


def _tradable(raw_json, policy):
    """Stock liquidity as a cheap stand-in for liquid options; the selector still checks contracts."""
    try:
        quote = json.loads(raw_json or '{}')
        return (quote.get('quote_type') == 'EQUITY'
                and float(quote.get('last_price') or 0) >= policy.market_event_min_price
                and float(quote.get('three_month_average_volume') or 0) >= policy.market_event_min_average_volume)
    except (TypeError, ValueError):
        return False


def _priority(filing, company):
    importance = max((ITEM_PRIORITY.get(item, 0) for item in filing['items']), default=0)
    return -importance, -(company['market_weight'] or 0), filing['accession']


def register_market_events(filings, *, issuer_map, policy, at, lifetime_seconds, db_path=None):
    symbols_by_cik = {}
    for symbol, cik in issuer_map.items():
        symbols_by_cik.setdefault(cik, []).append(symbol)
    skipped, eligible, chosen = Counter(), [], {}
    with closing(get_connection(db_path)) as conn, conn:
        active = active_company_ids(conn, at=at)
        companies = {row['symbol'].upper().replace('.', '-'): row for row in
                     conn.execute('SELECT id,symbol,market_weight,raw_json FROM companies')}
        for filing in filings:
            # Share classes and preferreds share one CIK; the companies table holds one of them.
            company = next((companies[symbol] for symbol in symbols_by_cik.get(filing['cik'], [])
                            if symbol in companies), None)
            if company is None:
                skipped['unknown_company'] += 1
            elif company['id'] in active:
                skipped['already_interesting'] += 1
            elif not _tradable(company['raw_json'], policy):
                skipped['below_tradability_floor'] += 1
            else:
                eligible.append((filing, company))
        for filing, company in sorted(eligible, key=lambda pair: _priority(*pair)):
            if company['id'] in chosen:
                skipped['repeat_filing'] += 1
            elif len(chosen) >= policy.market_event_limit:
                skipped['cycle_limit'] += 1
            else:
                chosen[company['id']] = company['symbol']
                register_interest(conn, company['id'], SOURCE, filing['accession'], at=at,
                                  lifetime_seconds=lifetime_seconds)
    return {'registered': list(chosen.values()), 'skipped': dict(skipped)}


def collect_market_events(*, policy=None, feed=None, issuer_map=None, clock=utc_now, db_path=None):
    """Collector step: read filings newer than the saved cursor and register interest for the best few."""
    policy = policy or DiscoveryPolicy.from_env()
    if policy.mode == 'disabled' or policy.market_event_limit == 0:
        return {'status': 'disabled'}
    if feed is None or issuer_map is None:
        user_agent = os.getenv('SEC_USER_AGENT', '').strip()
        if '@' not in user_agent:
            return {'status': 'configuration_required', 'reason': 'SEC_USER_AGENT must identify the operator and contact email'}
        evidence_policy = CompanyEvidencePolicy.from_env()
        http = EvidenceHTTP('sec', ('www.sec.gov', 'data.sec.gov'), evidence_policy, db_path, user_agent, clock=clock)
        feed = feed or SECCurrentFilings(http)
        issuer_map = issuer_map or SECAdapter(http, evidence_policy).issuer_map()
    initialize_database(db_path=db_path)
    at = clock().isoformat()
    since = collection_state(*FEED_STATE, db_path=db_path).get('latest_filed_at')
    try:
        filings = feed(since=since)
    except Exception as exc:
        return {'status': 'provider_unavailable', 'reason': f'sec_current_feed:{type(exc).__name__}'}
    if since:
        filings = [f for f in filings if datetime.fromisoformat(f['filed_at']) > datetime.fromisoformat(since)]
    result = register_market_events(filings, issuer_map=issuer_map, policy=policy, at=at,
                                    lifetime_seconds=policy.stale_seconds, db_path=db_path)
    if filings:
        latest = max(filings, key=lambda f: datetime.fromisoformat(f['filed_at']))['filed_at']
        save_collection_state(*FEED_STATE, {'latest_filed_at': latest}, at, db_path=db_path)
    return {'status': 'collected', 'filings': len(filings), **result}
