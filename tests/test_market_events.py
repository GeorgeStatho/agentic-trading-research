"""Market-wide SEC filings bring companies outside the static ranking into discovery's interest."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime
import json
import unittest
from unittest.mock import Mock

from db_common import get_connection
from tests.research_foundation_fixtures import database, START, ENTRY
from services.discovery.discovery_policy import DiscoveryPolicy

LIQUID = {'last_price': 50.0, 'three_month_average_volume': 2_000_000, 'quote_type': 'EQUITY'}
ATOM = '''<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Latest Filings</title>
<entry>
<title>8-K - Liquid Co (0000000003) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/3/000000000326000001/0000000003-26-000001-index.htm"/>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-09-02 &lt;b&gt;AccNo:&lt;/b&gt; 0000000003-26-000001 &lt;b&gt;Size:&lt;/b&gt; 1 MB
&lt;br&gt;Item 2.02: Results of Operations and Financial Condition
&lt;br&gt;Item 9.01: Financial Statements and Exhibits</summary>
<updated>2026-09-02T16:05:00-04:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="8-K"/>
<id>urn:tag:sec.gov,2008:accession-number=0000000003-26-000001</id>
</entry>
<entry>
<title>8-K - Liquid Co (0000000003) (Subject)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/3/000000000326000001/0000000003-26-000001-index.htm"/>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-09-02 &lt;b&gt;AccNo:&lt;/b&gt; 0000000003-26-000001</summary>
<updated>2026-09-02T16:05:00-04:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="8-K"/>
<id>urn:tag:sec.gov,2008:accession-number=0000000003-26-000001</id>
</entry>
</feed>'''


def filing(cik, accession, *, items=('8.01',), filed_at=ENTRY, form='8-K'):
    return {'cik': cik, 'form': form, 'accession': accession, 'filed_at': filed_at, 'items': list(items)}


class CurrentFeedParsingTests(unittest.TestCase):
    def test_parses_filer_form_accession_time_and_items_once_per_filing(self):
        from Webscraping.company_evidence.sec_current import parse_current_filings
        self.assertEqual(parse_current_filings(ATOM), [{
            'cik': '0000000003', 'form': '8-K', 'accession': '0000000003-26-000001',
            'filed_at': '2026-09-02T20:05:00+00:00', 'items': ['2.02', '9.01']}])


class MarketEventInterestTests(unittest.TestCase):
    def setUp(self):
        self.db = database(self)
        with closing(get_connection(self.db)) as conn, conn:
            rows = [(3, 'LIQ', 'Liquid Co', .5, LIQUID), (4, 'BIG', 'Big Co', .9, LIQUID),
                    (5, 'THIN', 'Thin Co', .1, {**LIQUID, 'three_month_average_volume': 10_000}),
                    (6, 'PENNY', 'Penny Co', .1, {**LIQUID, 'last_price': 2.0}), (7, 'BARE', 'Bare Co', .1, None)]
            for company_id, symbol, name, weight, raw in rows:
                conn.execute('INSERT INTO companies(id,industry_id,symbol,name,market_weight,raw_json) VALUES (?,1,?,?,?,?)',
                             (company_id, symbol, name, weight, json.dumps(raw) if raw else None))
            conn.execute('UPDATE companies SET raw_json=? WHERE symbol=?', (json.dumps(LIQUID), 'NVDA'))
            from market_db_modules.company_interest import register_interest
            register_interest(conn, 1, 'pipeline_selection', 'industry:chips', at=START, lifetime_seconds=604800)
        self.issuers = {'LIQ': '0000000003', 'BIG': '0000000004', 'THIN': '0000000005', 'PENNY': '0000000006',
                        'BARE': '0000000007', 'NVDA': '0001045810'}
        self.policy = DiscoveryPolicy(mode='shadow', market_event_limit=5)

    def register(self, filings, policy=None):
        from services.discovery.market_events import register_market_events
        return register_market_events(filings, issuer_map=self.issuers, policy=policy or self.policy,
                                      at=ENTRY, lifetime_seconds=604800, db_path=self.db)

    def interest(self):
        with closing(get_connection(self.db)) as conn:
            return {row['symbol']: row['source_key'] for row in conn.execute('''SELECT c.symbol,i.source_key
                FROM company_interest i JOIN companies c ON c.id=i.company_id WHERE i.source='market_event' ''')}

    def test_a_liquid_known_filer_gains_market_event_interest(self):
        result = self.register([filing('0000000003', '0000000003-26-000001')])
        self.assertEqual(self.interest(), {'LIQ': '0000000003-26-000001'})
        self.assertEqual(result['registered'], ['LIQ'])

    def test_illiquid_unmapped_and_already_interesting_filers_are_skipped_with_reasons(self):
        result = self.register([filing('0000000005', 'a-1'), filing('0000000006', 'a-2'), filing('0000000007', 'a-3'),
                                filing('0009999999', 'a-4'), filing('0001045810', 'a-5')])
        self.assertEqual(self.interest(), {})
        self.assertEqual(result['skipped'], {'below_tradability_floor': 3, 'unknown_company': 1, 'already_interesting': 1})

    def test_the_cycle_limit_keeps_earnings_first_then_the_heavier_market_weight(self):
        result = self.register([filing('0000000004', 'b-1'), filing('0000000003', 'b-2', items=('2.02',))],
                               policy=replace(self.policy, market_event_limit=1))
        self.assertEqual((result['registered'], result['skipped']), (['LIQ'], {'cycle_limit': 1}))

    def test_registration_makes_the_company_due_for_evidence_and_a_discovery_scan(self):
        from services.discovery.company_evidence_demand import enqueue_company_interest
        from services.discovery.company_evidence_policy import CompanyEvidencePolicy
        self.register([filing('0000000003', '0000000003-26-000001')])
        enqueue_company_interest(at=ENTRY, policy=CompanyEvidencePolicy(), db_path=self.db)
        with closing(get_connection(self.db)) as conn:
            self.assertTrue(conn.execute("SELECT 1 FROM company_evidence_requests WHERE company_id=3 AND status='pending'").fetchone())
            self.assertEqual(conn.execute('SELECT status FROM discovery_checks WHERE company_id=3').fetchone()[0], 'pending')

    def collect(self, feed, policy=None):
        from services.discovery.market_events import collect_market_events
        return collect_market_events(policy=policy or self.policy, feed=feed, issuer_map=self.issuers,
                                     clock=lambda: datetime.fromisoformat(ENTRY), db_path=self.db)

    def test_off_by_default_and_never_fetches(self):
        feed = Mock(return_value=[])
        self.assertEqual(self.collect(feed, policy=DiscoveryPolicy(mode='shadow'))['status'], 'disabled')
        self.assertEqual(self.collect(feed, policy=DiscoveryPolicy(market_event_limit=5))['status'], 'disabled')
        feed.assert_not_called()

    def test_filings_at_or_before_the_saved_cursor_are_not_reconsidered(self):
        self.collect(Mock(return_value=[filing('0000000003', 'c-1', filed_at=START)]))
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("DELETE FROM company_interest WHERE source='market_event'")
        result = self.collect(Mock(return_value=[filing('0000000003', 'c-1', filed_at=START),
                                                 filing('0000000004', 'c-2', filed_at=ENTRY)]))
        self.assertEqual((result['registered'], self.interest()), (['BIG'], {'BIG': 'c-2'}))


class CollectorWiringTests(unittest.TestCase):
    def test_the_collector_registers_market_events_before_collecting_evidence(self):
        import importlib
        from contextlib import ExitStack
        from unittest.mock import patch
        collector = importlib.import_module('news_collector_main')
        calls = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(collector, 'run_news_collection_pipeline', return_value={}))
            stack.enter_context(patch.object(collector.JsonFileWriter, 'write'))
            for target in ('services.gates.market_regime.build_market_regime_snapshot',
                           'services.discovery.catalyst_response_collector.collect_catalyst_responses',
                           'services.evaluation.decision_attribution_runner.attribute_decisions',
                           'services.evaluation.forward_evaluation.run_forward_evaluation_cycle',
                           'services.discovery.event_discovery.discover_company_events',
                           'services.discovery.discovery_queue.run_discovery_research_cycle'):
                stack.enter_context(patch(target, return_value={}))
            for name, target in (('market_events', 'services.discovery.market_events.collect_market_events'),
                                 ('evidence', 'services.discovery.company_evidence_service.collect_company_evidence')):
                stack.enter_context(patch(target, side_effect=lambda name=name: calls.append(name) or {}))
            result = collector.run_news_collection_cycle()
        self.assertEqual(calls, ['market_events', 'evidence'])
        self.assertIn('market_events', result)


if __name__ == '__main__':
    unittest.main()
