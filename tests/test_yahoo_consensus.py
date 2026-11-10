"""Yahoo consensus adapter, its package freshness rules and collector wiring; fully offline."""
from datetime import date, datetime, timedelta
import json
import os
import unittest
from unittest.mock import Mock, patch

from contextlib import closing

from agent_builders.company_evidence_payload import build_company_evidence_payload
from db_common import get_connection
from tests.research_foundation_fixtures import database, ENTRY
from tests.test_company_evidence import COMPANY, guidance
from market_db_modules.company_evidence import get_company_fact, get_evidence_package
from market_db_modules.foundation_store import digest
from services.discovery.company_evidence_extraction import ingest_source, validate_extracted_fact
from services.discovery.company_evidence_packages import build_company_package
from services.discovery.company_evidence_policy import CompanyEvidencePolicy, provider_manifest
from services.discovery.company_evidence_service import collect_company, collect_company_evidence
from Webscraping.company_evidence.models import CollectionResult, SourceDocument
from Webscraping.company_evidence.yahoo_consensus import YahooConsensusAdapter


NOW = datetime.fromisoformat(ENTRY)
MSFT = {'Dividend Date': date(2026, 12, 9), 'Ex-Dividend Date': date(2026, 11, 18),
        'Earnings Date': [date(2026, 10, 28)], 'Earnings High': 4.86, 'Earnings Low': 4.43,
        'Earnings Average': 4.72072, 'Revenue High': 92655000000, 'Revenue Low': 89640000000,
        'Revenue Average': 90736182420}
MSFT_COMPANY = {**COMPANY, 'symbol': 'MSFT'}
EVENT = {'name': 'Earnings release', 'scheduled_at': '2026-10-28', 'precision': 'date', 'confirmed': False,
         'status': 'yahoo_calendar_estimate', 'uncertainty': 'time_not_supplied'}


class FakeTicker:
    """Stands in for yfinance.Ticker; counts `calendar` reads so the per-collection rate is checkable."""
    def __init__(self, calendar=None, error=None, info=None):
        self._calendar, self.error, self.reads, self._info = calendar, error, 0, info

    @property
    def calendar(self):
        self.reads += 1
        if self.error:
            raise self.error
        return self._calendar

    @property
    def info(self):
        if isinstance(self._info, Exception):
            raise self._info
        return self._info or {}


def adapter(calendar=None, error=None, clock=lambda: NOW, info=None):
    ticker = FakeTicker(MSFT if calendar is None else calendar, error, info)
    return YahooConsensusAdapter(ticker_factory=lambda symbol: ticker, clock=clock), ticker


def consensus_doc(at, *, value=4.72072, period='2026-10-28'):
    """A consensus-typed document built without the adapter, so package rules are tested on their own."""
    text = f'USD analyst consensus eps_diluted next_report:{period} average {value}'
    fact = {'metric': 'eps_diluted', 'kind': 'consensus', 'value': value, 'unit': 'USD/shares', 'currency': 'USD',
            'scale': 1, 'fiscal_period': f'next_report:{period}', 'period_type': 'quarter',
            'accounting_basis': 'analyst_consensus', 'source_start': 0, 'source_end': len(text),
            'extraction_version': 'yahoo-calendar-consensus-v1'}
    return SourceDocument('yahoo_consensus', 'https://finance.yahoo.com/quote/NVDA/analysis', 'Yahoo Finance',
                          'consensus', text, at, at, f'yahoo_consensus:NVDA:{period}',
                          {'cik': COMPANY['cik'], 'publication_precision': 'timestamp'}, (fact,), (EVENT,))


class AdapterTests(unittest.TestCase):
    def test_calendar_becomes_one_document_with_facts_and_event(self):
        yahoo, ticker = adapter()
        result = yahoo.collect(MSFT_COMPANY, {})
        self.assertEqual(yahoo.name, 'yahoo_consensus')
        self.assertEqual(ticker.reads, 1)
        (doc,) = result.documents
        self.assertEqual((doc.provider, doc.publisher, doc.document_type),
                         ('yahoo_consensus', 'Yahoo Finance', 'consensus'))
        self.assertEqual(doc.url, 'https://finance.yahoo.com/quote/MSFT/analysis')
        self.assertEqual(doc.event_key, 'yahoo_consensus:MSFT:2026-10-28')
        self.assertEqual((doc.published_at, doc.observed_at), (NOW.isoformat(), NOW.isoformat()))
        self.assertEqual(doc.metadata['publication_precision'], 'timestamp')
        self.assertEqual(doc.metadata['cik'], MSFT_COMPANY['cik'])  # collect_company rejects issuer mismatches
        self.assertIn('source', doc.metadata)
        self.assertEqual(doc.scheduled_events, (EVENT,))
        self.assertEqual(result.gaps + doc.gaps, ())

    def test_text_is_canonical_json_with_iso_dates(self):
        (doc,) = adapter()[0].collect(MSFT_COMPANY, {}).documents
        parsed = json.loads(doc.text)
        self.assertEqual(list(parsed), sorted(parsed))
        self.assertEqual(parsed['Earnings Date'], ['2026-10-28'])

    def test_facts_carry_values_and_their_text_slices(self):
        (doc,) = adapter()[0].collect(MSFT_COMPANY, {}).documents
        facts = {fact['metric']: fact for fact in doc.facts}
        self.assertEqual(set(facts), {'eps_diluted', 'revenue'})
        eps, revenue = facts['eps_diluted'], facts['revenue']
        self.assertEqual((eps['value'], eps['range_high'], eps['unit']), (4.72072, 4.86, 'USD/shares'))
        self.assertEqual((revenue['value'], revenue['range_high'], revenue['unit']),
                         (90736182420, 92655000000, 'USD'))
        self.assertNotIn('range_low', eps)  # CompanyFact has no such field; it would fail at storage
        for fact in facts.values():
            self.assertEqual((fact['kind'], fact['extraction_version'], fact['accounting_basis']),
                             ('consensus', 'yahoo-calendar-consensus-v1', 'analyst_consensus'))
            self.assertEqual((fact['currency'], fact['scale'], fact['period_type'], fact['fiscal_period']),
                             ('USD', 1, 'quarter', 'next_report:2026-10-28'))
            excerpt = doc.text[fact['source_start']:fact['source_end']]
            self.assertIn(str(fact['value']), excerpt)
            self.assertIn(str(fact['range_high']), excerpt)

    def test_facts_survive_extraction_validation(self):
        # Period label and USD must be backed by the text, exactly as for every other provider's facts.
        (doc,) = adapter()[0].collect(MSFT_COMPANY, {}).documents
        for fact in doc.facts:
            validate_extracted_fact(fact, doc.text, MSFT_COMPANY)

    def test_unavailable_calendars_return_a_gap_not_an_exception(self):
        cases = {'empty': {}, 'raises': RuntimeError('rate limited'), 'no_usable_fields': {'Dividend Date': date(2026, 12, 9)}}
        for name, case in cases.items():
            with self.subTest(name):
                yahoo, _ = adapter(case) if not isinstance(case, Exception) else adapter(error=case)
                result = yahoo.collect(MSFT_COMPANY, {})
                self.assertEqual(result.documents, ())
                self.assertIn('consensus_provider_unavailable', result.gaps)

    def test_non_usd_reporter_yields_no_consensus(self):
        # 2026-11-09: SK hynix's revenue consensus (1.3e14, in won) was stored as USD.
        yahoo, _ = adapter(info={'financialCurrency': 'KRW'})
        result = yahoo.collect(MSFT_COMPANY, {})
        self.assertEqual((result.documents, result.gaps), ((), ('consensus_currency_not_usd',)))

    def test_usd_or_unknown_currency_collects_as_before(self):
        for info in ({'financialCurrency': 'USD'}, {}, RuntimeError('info unavailable')):
            with self.subTest(info=info):
                yahoo, _ = adapter(info=info)
                (doc,) = yahoo.collect(MSFT_COMPANY, {}).documents
                self.assertEqual({fact['currency'] for fact in doc.facts}, {'USD'})

    def test_ticker_construction_failure_is_also_a_gap(self):
        def broken(symbol):
            raise ConnectionError('no network')
        result = YahooConsensusAdapter(ticker_factory=broken, clock=lambda: NOW).collect(MSFT_COMPANY, {})
        self.assertEqual((result.documents, result.gaps), ((), ('consensus_provider_unavailable',)))

    def test_missing_revenue_keys_leave_only_eps(self):
        calendar = {k: v for k, v in MSFT.items() if not k.startswith('Revenue')}
        (doc,) = adapter(calendar)[0].collect(MSFT_COMPANY, {}).documents
        self.assertEqual([fact['metric'] for fact in doc.facts], ['eps_diluted'])
        self.assertEqual(doc.scheduled_events, (EVENT,))

    def test_missing_eps_average_leaves_only_revenue(self):
        calendar = {k: v for k, v in MSFT.items() if k != 'Earnings Average'}
        (doc,) = adapter(calendar)[0].collect(MSFT_COMPANY, {}).documents
        self.assertEqual([fact['metric'] for fact in doc.facts], ['revenue'])


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.db = database(self)
        self.policy = CompanyEvidencePolicy(stale_seconds=7 * 86400)
        self.primary_at = (NOW - timedelta(days=30)).isoformat()

    def package(self, sources, at=ENTRY):
        ids = [ingest_source(COMPANY, source, at, db_path=self.db)[0] for source in sources]
        return build_company_package(COMPANY, ids, at, self.policy, db_path=self.db)

    def test_consensus_fills_gaps_but_never_becomes_the_newest_document(self):
        identity = self.package([guidance(at=self.primary_at), consensus_doc(ENTRY)])
        package = get_evidence_package(identity, db_path=self.db)
        self.assertFalse([gap for gap in package['gaps'] if gap.startswith('consensus_unavailable')])
        self.assertNotIn('scheduled_earnings_event_unavailable', package['gaps'])
        self.assertEqual([e['scheduled_at'] for e in package['scheduled_events']], ['2026-10-28'])
        self.assertEqual(package['freshness_status'], 'stale')
        self.assertEqual(package['catalyst_id'], 'catalyst:company-evidence:' + digest([COMPANY['id'], 'earnings-q3']))

    def test_consensus_only_evidence_builds_no_package(self):
        self.assertIsNone(self.package([consensus_doc(ENTRY)]))

    def test_repeated_consensus_does_not_evict_primary_documents(self):
        # Each refresh stores a new consensus document; they must not push the filing out of the 24-document window.
        primary_id = ingest_source(COMPANY, guidance(at=self.primary_at), ENTRY, db_path=self.db)[0]
        ids = [primary_id]
        for hour in range(1, 31):
            at = (NOW + timedelta(hours=hour)).isoformat()
            ids.append(ingest_source(COMPANY, consensus_doc(at, value=4.7 + hour / 1000), at, db_path=self.db)[0])
        end = (NOW + timedelta(hours=31)).isoformat()
        identity = build_company_package(COMPANY, ids, end, self.policy, db_path=self.db)
        self.assertIn(primary_id, get_evidence_package(identity, db_path=self.db)['document_ids'])

    def test_newer_consensus_supersedes_the_older_fact_for_the_same_period(self):
        first = ingest_source(COMPANY, consensus_doc(ENTRY, value=4.70), ENTRY, db_path=self.db)[1][0]
        later = (NOW + timedelta(hours=3)).isoformat()
        second = ingest_source(COMPANY, consensus_doc(later, value=4.75), later, db_path=self.db)[1][0]
        self.assertEqual(get_company_fact(second, db_path=self.db)['supersedes_fact_id'], first)

    def test_adapter_refreshes_supersede_through_real_ingestion(self):
        # The estimates moved between refreshes; an unchanged calendar is covered by StableIdentityTests.
        revised = {**MSFT, 'Earnings Average': 4.80, 'Revenue Average': 91000000000}
        facts = []
        for clock, calendar in ((NOW, MSFT), (NOW + timedelta(hours=3), revised)):
            yahoo, _ = adapter(calendar, clock=lambda clock=clock: clock)
            (doc,) = yahoo.collect(COMPANY, {}).documents
            facts.append(dict(zip(('eps_diluted', 'revenue'),
                                  [get_company_fact(f, db_path=self.db) for f in
                                   ingest_source(COMPANY, doc, clock.isoformat(), db_path=self.db)[1]])))
        for metric in ('eps_diluted', 'revenue'):
            self.assertEqual(facts[1][metric]['supersedes_fact_id'], facts[0][metric]['fact_id'])


class StableIdentityTests(unittest.TestCase):
    """Refreshing an unchanged calendar must not mint new documents, facts or package revisions."""
    def setUp(self):
        self.db = database(self)
        self.policy = CompanyEvidencePolicy(stale_seconds=7 * 86400)

    def ingest(self, calendar, at):
        yahoo, _ = adapter(calendar, clock=lambda: at)
        (doc,) = yahoo.collect(COMPANY, {}).documents
        return ingest_source(COMPANY, doc, at.isoformat(), db_path=self.db)

    def consensus_rows(self):
        with closing(get_connection(self.db)) as conn:
            return conn.execute("SELECT COUNT(*) FROM evidence_documents WHERE provider='yahoo_consensus'").fetchone()[0]

    def test_unchanged_calendar_is_the_same_document_and_facts(self):
        first = self.ingest(MSFT, NOW)
        second = self.ingest(MSFT, NOW + timedelta(hours=3))
        self.assertEqual(first, second)
        self.assertEqual(self.consensus_rows(), 1)

    def test_changed_calendar_is_a_new_document(self):
        first = self.ingest(MSFT, NOW)
        second = self.ingest({**MSFT, 'Earnings Average': 4.80}, NOW + timedelta(hours=3))
        self.assertNotEqual(first[0], second[0])
        self.assertEqual(self.consensus_rows(), 2)

    def test_package_revision_is_stable_across_unchanged_refreshes(self):
        primary = ingest_source(COMPANY, guidance(at=(NOW - timedelta(days=30)).isoformat()), ENTRY, db_path=self.db)[0]
        revisions = []
        for hours in (0, 3):
            at = NOW + timedelta(hours=hours)
            consensus = self.ingest(MSFT, at)[0]
            identity = build_company_package(COMPANY, [primary, consensus], at.isoformat(), self.policy, db_path=self.db)
            revisions.append(get_evidence_package(identity, db_path=self.db)['event_revision'])
        self.assertEqual(revisions[0], revisions[1])


class PayloadFreshnessTests(unittest.TestCase):
    """freshness_at_read ages the primary evidence, not the consensus fetched alongside it."""
    def setUp(self):
        self.db = database(self)
        self.policy = CompanyEvidencePolicy(stale_seconds=7 * 86400)

    def test_consensus_does_not_keep_stale_primary_evidence_fresh(self):
        primary = ingest_source(COMPANY, guidance(at=(NOW - timedelta(days=6)).isoformat()), ENTRY, db_path=self.db)[0]
        consensus = ingest_source(COMPANY, consensus_doc(ENTRY), ENTRY, db_path=self.db)[0]
        identity = build_company_package(COMPANY, [primary, consensus], ENTRY, self.policy, db_path=self.db)
        self.assertEqual(get_evidence_package(identity, db_path=self.db)['freshness_status'], 'fresh')
        read_at = (NOW + timedelta(days=3)).isoformat()  # primary is now 9 days old, consensus only 3
        with patch.dict(os.environ, {'COMPANY_EVIDENCE_STALE_SECONDS': str(7 * 86400)}):
            payload = build_company_evidence_payload(COMPANY['id'], package_id=identity, as_of=read_at, db_path=self.db)
        self.assertEqual(payload['freshness_at_read'], 'stale')


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.db = database(self)
        self.policy = CompanyEvidencePolicy(stale_seconds=7 * 86400)
        self.primary = Mock(name='primary')
        self.primary.name = 'fixture'
        self.primary.collect.return_value = CollectionResult(
            (guidance(at=(NOW - timedelta(days=30)).isoformat()),), {'last': 'release-1'})
        self.now = NOW

    def collect(self, yahoo):
        return collect_company(COMPANY, [self.primary, yahoo], self.policy, db_path=self.db, clock=lambda: self.now)

    def test_collected_consensus_reaches_the_package(self):
        yahoo, _ = adapter()
        package = get_evidence_package(self.collect(yahoo)['package_id'], db_path=self.db)
        kinds = {get_company_fact(f, db_path=self.db)['kind'] for f in package['fact_ids']}
        self.assertIn('consensus', kinds)
        self.assertEqual(package['freshness_status'], 'stale')
        self.assertFalse([gap for gap in package['gaps'] if gap.startswith('consensus_')])
        self.assertEqual([e['scheduled_at'] for e in package['scheduled_events']], ['2026-10-28'])

    def test_unavailable_consensus_is_reported_in_the_package(self):
        yahoo, _ = adapter(error=RuntimeError('blocked'))
        result = self.collect(yahoo)
        self.assertEqual(result['providers']['yahoo_consensus'], 'collected')
        self.assertIn('consensus_provider_unavailable',
                      get_evidence_package(result['package_id'], db_path=self.db)['gaps'])

    def test_one_calendar_read_per_company_within_the_refresh_window(self):
        yahoo, ticker = adapter()
        self.collect(yahoo)
        self.now += timedelta(seconds=self.policy.refresh_seconds - 1)
        self.assertEqual(self.collect(yahoo)['providers']['yahoo_consensus'], 'cached')
        self.assertEqual(ticker.reads, 1)
        self.now += timedelta(seconds=2)
        self.assertEqual(self.collect(yahoo)['providers']['yahoo_consensus'], 'collected')
        self.assertEqual(ticker.reads, 2)


class WiringTests(unittest.TestCase):
    """Run the real collector entry point with SEC and the per-company work replaced."""
    def setUp(self):
        self.db = database(self)
        patches = [patch('services.discovery.company_evidence_service.SECAdapter', return_value=Mock(issuer_map=lambda: {'NVDA': '0001045810'})),
                   patch('services.discovery.company_evidence_service.EvidenceHTTP'),
                   patch('services.discovery.company_evidence_service.enqueue_company_interest'),
                   patch('services.discovery.company_evidence_service.due_refresh_companies',
                         return_value=[{'id': 1, 'symbol': 'NVDA', 'name': 'Fixture'}]),
                   patch('services.discovery.company_evidence_service.collect_company',
                         return_value={'package_id': 'package:x', 'providers': {}})]
        self.sec_cls, _, _, _, self.collect = [self.enterContext(p) for p in patches]
        self.yahoo_cls = self.enterContext(patch('services.discovery.company_evidence_service.YahooConsensusAdapter'))
        self.enterContext(patch.dict(os.environ, {'SEC_USER_AGENT': 'tester test@example.test'}))

    def adapters(self, consensus, **kwargs):
        policy = CompanyEvidencePolicy(enabled=True, consensus=consensus)
        collect_company_evidence(db_path=self.db, policy=policy, **kwargs)
        return self.collect.call_args.args[1]

    def test_yahoo_policy_collects_with_yahoo(self):
        self.assertEqual(self.adapters('yahoo'), [self.sec_cls.return_value, self.yahoo_cls.return_value])

    def test_none_policy_disables_the_consensus_adapter(self):
        self.assertEqual(self.adapters('none'), [self.sec_cls.return_value])
        self.yahoo_cls.assert_not_called()

    def test_explicit_adapter_wins_over_the_policy(self):
        explicit = Mock(name='licensed')
        self.assertEqual(self.adapters('yahoo', consensus_adapter=explicit), [self.sec_cls.return_value, explicit])
        self.yahoo_cls.assert_not_called()


class PolicyTests(unittest.TestCase):
    def test_from_env_reads_the_consensus_setting(self):
        for value, expected in ((None, 'yahoo'), ('yahoo', 'yahoo'), ('none', 'none'), ('', 'none'), (' NONE ', 'none')):
            with self.subTest(value), patch.dict(os.environ):
                os.environ.pop('COMPANY_EVIDENCE_CONSENSUS', None)
                if value is not None:
                    os.environ['COMPANY_EVIDENCE_CONSENSUS'] = value
                self.assertEqual(CompanyEvidencePolicy.from_env().consensus, expected)

    def test_common_off_words_disable_consensus(self):
        for value in ('off', 'false', '0', 'no', 'disabled', 'OFF'):
            with self.subTest(value), patch.dict(os.environ, {'COMPANY_EVIDENCE_CONSENSUS': value}):
                self.assertEqual(CompanyEvidencePolicy.from_env().consensus, 'none')

    def test_unknown_provider_still_fails_fast(self):
        with patch.dict(os.environ, {'COMPANY_EVIDENCE_CONSENSUS': 'yahooo'}):
            with self.assertRaisesRegex(ValueError, 'consensus'):
                CompanyEvidencePolicy.from_env()


class ManifestTests(unittest.TestCase):
    def test_consensus_defaults_to_yahoo(self):
        with patch.dict(os.environ):
            os.environ.pop('COMPANY_EVIDENCE_CONSENSUS', None)
            manifest = provider_manifest()
        self.assertEqual(manifest['consensus'], 'yahoo')
        self.assertIn('yahoo', manifest['policy'].values())  # the setting is part of the recorded policy

    def test_disabled_consensus_is_unconfigured(self):
        for value in ('none', ''):
            with self.subTest(value), patch.dict(os.environ, {'COMPANY_EVIDENCE_CONSENSUS': value}):
                self.assertEqual(provider_manifest()['consensus'], 'unconfigured')


if __name__ == '__main__':
    unittest.main()
