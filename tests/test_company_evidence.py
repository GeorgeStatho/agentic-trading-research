"""Offline acceptance coverage for Phase 1; no broker, model or live provider access."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from tests.research_foundation_fixtures import database, START, ENTRY, END
from market_db_modules.company_collection import cached_response, collection_state
from market_db_modules.company_evidence import (
    get_company_fact,
    get_evidence_package,
    latest_evidence_package,
)
from services.company_evidence_extraction import ingest_source, validate_extracted_fact
from services.company_evidence_packages import build_company_package
from services.company_evidence_policy import CompanyEvidencePolicy, provider_manifest
from services.company_evidence_service import collect_company, collect_company_evidence
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


if __name__ == '__main__':
    unittest.main()
