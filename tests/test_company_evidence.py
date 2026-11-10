"""Offline acceptance coverage for Phase 1; no broker, model or live provider access."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from tests.research_foundation_fixtures import database, START, ENTRY, END
from db_common import get_connection
from market_db_modules.company_collection import collection_state
from market_db_modules.company_evidence import (
    get_evidence_package,
)
from services.discovery.company_evidence_extraction import ingest_source, validate_extracted_fact
from services.discovery.company_evidence_packages import build_company_package
from services.discovery.company_evidence_policy import CompanyEvidencePolicy, provider_manifest
from services.discovery.company_evidence_service import collect_company, collect_company_evidence
from agent_builders.company_evidence_payload import build_company_evidence_payload
from Webscraping.company_evidence.models import CollectionResult, SourceDocument
from Webscraping.company_evidence.releases import guidance_facts
from Webscraping.company_evidence.sec import SECAdapter
from Webscraping.company_evidence.sec_facts import normalize_company_facts
from Webscraping.company_evidence.transport import EvidenceHTTP, ProviderUnavailable


COMPANY = {'id': 1, 'symbol': 'NVDA', 'name': 'Fixture', 'cik': '0001045810'}
ACCESSION = '0001045810-26-000001'


def guidance(low=100, high=110, *, at=START, period='Q3 FY2026', basis='GAAP', kind='current', url=None):
    text = f'Fixture {period} {basis} revenue {kind} guidance is USD ${low} to ${high} million.'
    return SourceDocument('fixture', url or 'https://issuer.test/release', 'Fixture', 'issuer_release',
                          text, at, at, 'earnings-q3', {'cik': COMPANY['cik']}, guidance_facts(text))


def actuals(value=100, *, at=START, tag='Revenues', accession=ACCESSION):
    payload = {'cik': 1045810, 'facts': {'us-gaap': {tag: {'units': {'USD': [
        {'start': '2026-04-01', 'end': '2026-06-30', 'val': value, 'accn': accession,
         'fy': 2026, 'fp': 'Q3', 'filed': at[:10]}
    ]}}}}}
    text, facts = normalize_company_facts(payload, COMPANY['cik'], {accession})
    return SourceDocument('fixture', 'https://issuer.test/facts', 'Fixture', 'xbrl_facts', text,
                          at, at, accession, {'cik': COMPANY['cik']}, facts)


class EvidenceAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.db = database(self)
        self.policy = CompanyEvidencePolicy()

    def package(self, sources, at=ENTRY, gaps=()):
        ids = [ingest_source(COMPANY, source, at, db_path=self.db)[0] for source in sources]
        return build_company_package(COMPANY, ids, at, self.policy, db_path=self.db, gaps=gaps)

    def test_restatement_does_not_change_earlier_package(self):
        identity = self.package([actuals()])
        original = build_company_evidence_payload(1, package_id=identity, as_of=ENTRY, db_path=self.db)
        self.package([actuals(90, at=END)], END)
        self.assertEqual(build_company_evidence_payload(1, as_of=ENTRY, db_path=self.db), original)

    def test_revised_guidance_records_midpoint_change(self):
        self.package([guidance()])
        identity = self.package([guidance(80, 90, at=END)], END)
        payload = build_company_evidence_payload(1, package_id=identity, as_of=END, db_path=self.db)
        self.assertEqual([f['value'] for f in payload['facts'] if f['kind'] == 'derived'], [-20])

    def test_incompatible_fiscal_periods_are_not_compared(self):
        self.package([guidance(period='FY2026', kind='prior')])
        identity = self.package([guidance(80, 90, at=END)], END)
        package = get_evidence_package(identity, db_path=self.db)
        self.assertEqual(package['opposing_claims'][0]['type'], 'missing_comparable_prior_guidance')

    def test_future_package_cannot_be_replayed_at_earlier_cutoff(self):
        identity = self.package([guidance(at=END)], END)
        with self.assertRaises(ValueError):
            build_company_evidence_payload(1, package_id=identity, as_of=ENTRY, db_path=self.db)

    def test_replay_rejects_another_company(self):
        identity = self.package([guidance()])
        with self.assertRaises(ValueError):
            build_company_evidence_payload(2, package_id=identity, as_of=ENTRY, db_path=self.db)

    def test_retry_reuses_documents_and_facts(self):
        source = guidance()
        first = ingest_source(COMPANY, source, ENTRY, db_path=self.db)
        self.assertEqual(ingest_source(COMPANY, source, END, db_path=self.db), first)


class ExtractionTests(unittest.TestCase):
    def test_sec_entity_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            normalize_company_facts({'cik': 999}, COMPANY['cik'], {ACCESSION})

    def test_number_not_in_source_rejected(self):
        source = guidance()
        with self.assertRaises(ValueError):
            validate_extracted_fact({**source.facts[0], 'value': 999}, source.text, COMPANY)

    def test_structured_entity_mismatch_rejected(self):
        source = guidance()
        with self.assertRaises(ValueError):
            validate_extracted_fact({**source.facts[0], 'company_id': 2}, source.text, COMPANY)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.db = database(self)
        self.now = datetime.fromisoformat(ENTRY)
        self.session = Mock()
        self.policy = CompanyEvidencePolicy(retries=2)
        self.sleep = Mock()
        self.http = EvidenceHTTP('test', ['issuer.test'], self.policy, self.db, 'fixture operator@example.test',
                                 session=self.session, clock=lambda: self.now, sleep=self.sleep)
        self.url = 'https://issuer.test/release'

    def response(self, body='release', status=200, headers=None):
        result = Mock(status_code=status, headers=headers or {}, encoding='utf-8')
        result.__enter__ = Mock(return_value=result)
        result.__exit__ = Mock(return_value=None)
        result.iter_content.return_value = [body.encode()]
        return result

    def test_cache_survives_transport_restart(self):
        self.session.get.return_value = self.response()
        first = self.http.get(self.url, ttl_seconds=3600)
        self.session.get.side_effect = AssertionError('No second request')
        self.assertEqual(self.http.get(self.url, ttl_seconds=3600), first)

    def test_daily_quota_stops_network_calls(self):
        self.http.policy = replace(self.policy, daily_requests=1)
        self.session.get.return_value = self.response(status=429)
        with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
            self.http.get(self.url)

    def test_long_retry_after_persists_cooldown(self):
        self.session.get.return_value = self.response(status=429, headers={'Retry-After': '120'})
        with self.assertRaises(ProviderUnavailable):
            self.http.get(self.url)
        self.session.get.side_effect = AssertionError('Cooldown must prevent retry')
        with self.assertRaisesRegex(ProviderUnavailable, 'cooldown is still active'):
            self.http.get(self.url)

    def test_outside_host_rejected_before_http(self):
        with self.assertRaises(ValueError):
            self.http.get('https://unconfigured.test/release')


class ProviderAdapterTests(unittest.TestCase):
    def setUp(self):
        self.db = database(self)
        self.http = Mock(provider='issuer:issuer.test')
        self.policy = CompanyEvidencePolicy()
        self.adapter = SECAdapter(self.http, self.policy)

    def response(self, url, body):
        return {'url': url, 'body': body if isinstance(body, str) else json.dumps(body),
                'observed_at': ENTRY, 'response_id': 'response:fixture'}

    def sec_responses(self, url, **kwargs):
        if '/submissions/' in url:
            return self.response(url, {'cik': 1045810, 'filings': {'recent': {
                'form': ['8-K'], 'accessionNumber': [ACCESSION], 'filingDate': ['2026-09-01'],
                'acceptanceDateTime': [START], 'reportDate': ['2026-06-30']}}})
        if '/companyfacts/' in url:
            return self.response(url, {'cik': 1045810, 'facts': {'us-gaap': {
                tag: {'units': {'USD': [{'start': '2026-04-01', 'end': '2026-06-30', 'val': number,
                                       'accn': ACCESSION, 'filed': '2026-09-01'}]}}
                for tag, number in [('Revenues', 100), ('GrossProfit', 60), ('OperatingIncomeLoss', 40)]}}})
        body = 'CENTRAL INDEX KEY: 0001045810\n<DOCUMENT><TYPE>8-K\n<TEXT><p>Results furnished in exhibit.</p></TEXT></DOCUMENT>'
        body += '<DOCUMENT><TYPE>EX-99.1\n<TEXT><p>' + guidance().text + '</p></TEXT></DOCUMENT>'
        return self.response(url, body)

    def test_sec_and_collector_build_margin_from_verified_actuals(self):
        self.http.get.side_effect = self.sec_responses
        result = collect_company(COMPANY, [self.adapter], self.policy, db_path=self.db,
                                 clock=lambda: datetime.fromisoformat(ENTRY))
        payload = build_company_evidence_payload(1, package_id=result['package_id'], as_of=ENTRY, db_path=self.db)
        self.assertEqual({fact['metric']: fact['value'] for fact in payload['facts'] if fact['kind'] == 'derived'},
                         {'gross_margin': 60, 'operating_margin': 40})


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.db = database(self)
        self.now = datetime.fromisoformat(ENTRY)
        self.policy = CompanyEvidencePolicy()
        self.adapter = Mock(name='adapter')
        self.adapter.name = 'fixture'
        self.adapter.collect.return_value = CollectionResult((guidance(),), {'last': 'release-1'})

    def collect(self):
        return collect_company(COMPANY, [self.adapter], self.policy, db_path=self.db, clock=lambda: self.now)

    def test_failure_preserves_last_successful_cursor(self):
        self.collect()
        self.now += timedelta(days=1)
        self.adapter.collect.side_effect = RuntimeError('provider unavailable')
        self.collect()
        self.assertEqual(collection_state('fixture', '1', db_path=self.db)['cursor'], {'last': 'release-1'})

    def test_disabled_collector_never_calls_provider(self):
        with patch('requests.Session.get', side_effect=AssertionError('Network forbidden')):
            result = collect_company_evidence(db_path=self.db, policy=self.policy)
        self.assertEqual(result['status'], 'disabled')

    def test_provider_manifest_excludes_contact(self):
        with patch.dict('os.environ', {'SEC_USER_AGENT': 'secret-name private@example.test'}):
            result = provider_manifest()
        self.assertNotIn('private@example.test', json.dumps(result))


class AgentEvidenceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.db = database(self)
        self.policy = CompanyEvidencePolicy()

    def saved_package(self):
        identity, _ = ingest_source(COMPANY, guidance(), ENTRY, db_path=self.db)
        return build_company_package(COMPANY, [identity], ENTRY, self.policy, db_path=self.db)

    def test_unmigrated_database_returns_explicit_unavailable(self):
        legacy = self.db.with_name('legacy.db')
        with closing(sqlite3.connect(legacy)) as conn, conn:
            conn.execute('CREATE TABLE companies (id INTEGER PRIMARY KEY)')
        self.assertEqual(build_company_evidence_payload(1, as_of=ENTRY, db_path=legacy)['coverage_status'], 'unavailable')

    def test_manager_handoff_preserves_frozen_package(self):
        from agent_stages.strategist_runner import _build_manager_evidence_payload
        from agent_stages.manager_runner import _apply_manager_evidence_payload
        package = self.saved_package()
        evidence = build_company_evidence_payload(1, package_id=package, as_of=ENTRY, db_path=self.db)
        strategist = {'company': {'symbol': 'NVDA'}, 'supporting_articles': {}, 'grounding': {}, 'company_evidence': evidence}
        frozen = _build_manager_evidence_payload(strategist)
        manager = {'company': {'symbol': 'NVDA'}, 'company_evidence': {'package_id': 'later-package'}}
        _apply_manager_evidence_payload(manager, frozen)
        strategist['company_evidence']['package_id'] = 'mutated-after-handoff'
        self.assertEqual(manager['company_evidence']['package_id'], package)


QUARTER = {'start': '2026-07-01', 'end': '2026-09-30'}


def companyfacts(tags, accession=ACCESSION):
    return {'cik': 1045810, 'facts': {'us-gaap': {
        tag: {'units': {'USD': [{**QUARTER, 'val': value, 'accn': accession, 'filed': '2026-11-03'}]}}
        for tag, value in tags.items()}}}


class RevenueTagTests(unittest.TestCase):
    """2026-11-10 review of cached SEC companyfacts: banks and some retailers had no revenue fact, others two."""

    def revenue(self, tags):
        _, facts = normalize_company_facts(companyfacts(tags), COMPANY['cik'], {ACCESSION})
        return [fact['value'] for fact in facts if fact['metric'] == 'revenue']

    def test_bank_revenue_net_of_interest_expense_is_revenue(self):
        self.assertEqual(self.revenue({'RevenuesNetOfInterestExpense': 50_170_000_000}), [50_170_000_000])  # JPM

    def test_contract_revenue_including_assessed_tax_is_revenue(self):
        self.assertEqual(self.revenue({'RevenueFromContractWithCustomerIncludingAssessedTax': 46_100_000_000}),
                         [46_100_000_000])  # VLO

    def test_one_revenue_fact_per_quarter_and_the_total_wins(self):
        # GM: total revenue 50.02B and contract revenue 45.71B were both stored as revenue.
        self.assertEqual(self.revenue({'Revenues': 50_020_000_000,
                                       'RevenueFromContractWithCustomerExcludingAssessedTax': 45_710_000_000}),
                         [50_020_000_000])

    def test_fee_income_below_net_interest_income_is_not_revenue(self):
        # HBAN: contract revenue 0.48B against net interest income 2.07B.
        self.assertEqual(self.revenue({'RevenueFromContractWithCustomerExcludingAssessedTax': 480_000_000,
                                       'InterestIncomeExpenseNet': 2_070_000_000}), [])

    def test_net_interest_income_never_becomes_a_fact(self):
        _, facts = normalize_company_facts(companyfacts({'InterestIncomeExpenseNet': 2_070_000_000}),
                                           COMPANY['cik'], {ACCESSION})
        self.assertEqual(facts, ())

    def test_the_extraction_version_records_the_new_rules(self):
        _, facts = normalize_company_facts(companyfacts({'Revenues': 1}), COMPANY['cik'], {ACCESSION})
        self.assertEqual({fact['extraction_version'] for fact in facts}, {'sec-standard-facts-v2'})


def facts_document(tags, at=START):
    text, facts = normalize_company_facts(companyfacts(tags), COMPANY['cik'], {ACCESSION})
    return SourceDocument('sec', 'https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json', 'Fixture',
                          'xbrl_facts', text, at, at, ACCESSION, {'cik': COMPANY['cik']}, facts)


class DocumentIdentityTests(unittest.TestCase):
    """JPM, 2026-11-10: one 10-Q stored six times, identical but for when it was fetched."""

    def setUp(self):
        self.db = database(self)

    def stored_documents(self):
        with closing(get_connection(self.db)) as conn:
            return conn.execute('SELECT COUNT(*) FROM evidence_documents').fetchone()[0]

    def test_the_same_text_fetched_later_is_the_same_document(self):
        first, _ = ingest_source(COMPANY, actuals(), ENTRY, db_path=self.db)
        again, _ = ingest_source(COMPANY, replace(actuals(), observed_at=END), END, db_path=self.db)
        self.assertEqual((again, self.stored_documents()), (first, 1))

    def test_a_package_holds_only_the_newest_version_of_a_document(self):
        old, old_facts = ingest_source(COMPANY, facts_document({'Revenues': 100}), ENTRY, db_path=self.db)
        new, new_facts = ingest_source(COMPANY, facts_document({'Revenues': 100, 'NetIncomeLoss': 20}), END,
                                       db_path=self.db)
        identity = build_company_package(COMPANY, [old, new], END, CompanyEvidencePolicy(), db_path=self.db)
        package = get_evidence_package(identity, db_path=self.db)
        self.assertEqual(package['document_ids'], [new])
        self.assertFalse(set(old_facts) & set(package['fact_ids']))


def release_8k(accession, at, release):
    cover = 'Item 2.02 Results of Operations and Financial Condition. See Exhibit 99.1.'
    return SourceDocument('sec', f'https://www.sec.gov/Archives/edgar/data/1045810/{accession}.txt', 'Fixture',
                          'regulatory_filing', cover + '\n' + release, at, at, accession,
                          {'cik': COMPANY['cik'], 'form': '8-K', 'release_start': len(cover) + 1}, ())


class ReleaseExcerptTests(unittest.TestCase):
    """Between a release and the 10-Q the quarter's numbers exist only in the 8-K; the strategist saw none of it."""

    def test_the_newest_release_carries_an_excerpt_of_its_release_text(self):
        db = database(self)
        older = release_8k('0001045810-26-000201', START, 'Fixture reports second quarter results.')
        newer = release_8k('0001045810-26-000202', ENTRY, 'Fixture reports record third quarter revenue. ' * 120)
        ids = [ingest_source(COMPANY, source, ENTRY, db_path=db)[0] for source in (older, newer, actuals())]
        identity = build_company_package(COMPANY, ids, ENTRY, CompanyEvidencePolicy(), db_path=db)
        documents = {d['document_id']: d for d in
                     build_company_evidence_payload(1, package_id=identity, as_of=ENTRY, db_path=db)['documents']}
        start = newer.metadata['release_start']
        self.assertEqual(documents[ids[1]]['release_excerpt'], newer.text[start:start + 4000])
        self.assertNotIn('release_excerpt', documents[ids[0]])
        self.assertNotIn('release_excerpt', documents[ids[2]])


class VisibleFactTests(unittest.TestCase):
    """2026-11-09: 43 of 50 prompts showed no prior-year quarter. The 48-fact cap sorted by period string,
    and superseded duplicates took slots, so every 2025 comparative was cut."""

    @staticmethod
    def fact(fact_id, period, *, metric='revenue', kind='actual', period_type='quarter', supersedes=None):
        return {'fact_id': fact_id, 'metric': metric, 'kind': kind, 'fiscal_period': period, 'period_type': period_type,
                'available_at': '2026-11-04T00:00:00+00:00', 'value': 1.0, 'supersedes_fact_id': supersedes}

    def facts(self):
        # 52/53-week fiscal year: the year-ago quarter ends 2025-09-27, not on the same calendar date.
        latest, year_ago = '2026-06-28/2026-09-26', '2025-06-29/2025-09-27'
        rows = [self.fact('rev-latest', latest), self.fact('rev-year-ago', year_ago),
                self.fact('ni-latest', latest, metric='net_income'), self.fact('ni-year-ago', year_ago, metric='net_income'),
                self.fact('est', 'next_report:2027-01-28', kind='consensus')]
        # Newer-period filler (year-to-date and instants) that sorts ahead of every 2025 period.
        rows += [self.fact(f'ytd-{i}', f'2026-01-01/2026-09-{i:02d}', metric=f'm{i}', period_type='duration')
                 for i in range(1, 29)]
        # Superseded duplicates of current facts: old versions that the newer fact replaced.
        rows += [self.fact(f'old-{i}', '2026-03-29/2026-06-27', metric=f'q{i}') for i in range(20)]
        rows += [self.fact(f'new-{i}', '2026-03-29/2026-06-27', metric=f'q{i}', supersedes=f'old-{i}') for i in range(20)]
        return rows

    def test_superseded_facts_are_hidden_and_the_year_ago_quarter_kept(self):
        from agent_builders.company_evidence_payload import MAX_PAYLOAD_FACTS, _visible_facts
        shown = {f['fact_id'] for f in _visible_facts(self.facts(), referenced=set())}
        self.assertLessEqual(len(shown), MAX_PAYLOAD_FACTS)
        self.assertFalse({f'old-{i}' for i in range(20)} & shown)
        self.assertLessEqual({'rev-latest', 'rev-year-ago', 'ni-latest', 'ni-year-ago', 'est'}, shown)

    def test_a_superseded_fact_a_claim_cites_stays_visible(self):
        from agent_builders.company_evidence_payload import _visible_facts
        self.assertIn('old-3', {f['fact_id'] for f in _visible_facts(self.facts(), referenced={'old-3'})})


if __name__ == '__main__':
    unittest.main()
