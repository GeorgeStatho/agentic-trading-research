"""Offline acceptance coverage for Phase 1; no broker, model or live provider access."""
from dataclasses import replace
from datetime import datetime, timedelta
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from tests.research_foundation_fixtures import database, START, ENTRY, END
from db_common import get_connection
from market_db_modules.bootstrap import initialize_database
from market_db_modules.company_collection import (
    cached_response, collection_state, get_issuer_mapping, update_issuer_mapping,
)
from market_db_modules.company_evidence import (
    get_company_fact, get_evidence_document, get_evidence_package, latest_evidence_package, list_company_facts,
)
from services.company_evidence_extraction import ingest_source, validate_extracted_fact
from services.company_evidence_packages import build_company_package
from services.company_evidence_policy import CompanyEvidencePolicy, provider_manifest
from services.company_evidence_service import collect_company, collect_company_evidence
from agent_builders.company_evidence_payload import build_company_evidence_payload
from Webscraping.company_evidence.models import CollectionResult, SourceDocument
from Webscraping.company_evidence.releases import guidance_facts, scheduled_events
from Webscraping.company_evidence.sec import SECAdapter
from Webscraping.company_evidence.issuer import IssuerReleaseAdapter
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

    def test_missing_consensus_is_explicit(self):
        package = get_evidence_package(self.package([actuals()]), db_path=self.db)
        self.assertIn('consensus_unavailable; market_surprise_unknown', package['gaps'])

    def test_restatement_does_not_change_earlier_package(self):
        identity = self.package([actuals()])
        original = build_company_evidence_payload(1, package_id=identity, as_of=ENTRY, db_path=self.db)
        self.package([actuals(90, at=END)], END)
        self.assertEqual(build_company_evidence_payload(1, as_of=ENTRY, db_path=self.db), original)

    def test_restatement_links_prior_fact(self):
        self.package([actuals()])
        self.package([actuals(90, at=END)], END)
        facts = list_company_facts(1, as_of=END, db_path=self.db)
        self.assertEqual(facts[-1]['supersedes_fact_id'], facts[0]['fact_id'])

    def test_revised_guidance_records_midpoint_change(self):
        self.package([guidance()])
        identity = self.package([guidance(80, 90, at=END)], END)
        payload = build_company_evidence_payload(1, package_id=identity, as_of=END, db_path=self.db)
        self.assertEqual([f['value'] for f in payload['facts'] if f['kind'] == 'derived'], [-20])

    def test_guidance_reduction_is_opposing_evidence(self):
        self.package([guidance()])
        identity = self.package([guidance(80, 90, at=END)], END)
        package = get_evidence_package(identity, db_path=self.db)
        self.assertEqual(package['opposing_claims'][0]['direction'], 'decrease')

    def test_incompatible_fiscal_periods_are_not_compared(self):
        self.package([guidance(period='FY2026', kind='prior')])
        identity = self.package([guidance(80, 90, at=END)], END)
        package = get_evidence_package(identity, db_path=self.db)
        self.assertEqual(package['opposing_claims'][0]['type'], 'missing_comparable_prior_guidance')

    def test_incompatible_accounting_basis_is_not_compared(self):
        self.package([guidance(basis='adjusted', kind='prior')])
        identity = self.package([guidance(at=END)], END)
        package = get_evidence_package(identity, db_path=self.db)
        self.assertEqual(package['opposing_claims'][0]['type'], 'missing_comparable_prior_guidance')

    def test_simultaneous_conflicting_values_remain_visible(self):
        identity = self.package([guidance(url='https://issuer.test/a'), guidance(90, 95, url='https://issuer.test/b')])
        package = get_evidence_package(identity, db_path=self.db)
        self.assertIn('conflicting_values', [claim['type'] for claim in package['opposing_claims']])

    def test_document_revisions_are_immutable(self):
        first = self.package([guidance()])
        old_id = get_evidence_package(first, db_path=self.db)['document_ids'][0]
        self.package([guidance(80, 90, at=END)], END)
        self.assertIn('$100 to $110', get_evidence_document(old_id, db_path=self.db)['text'])

    def test_replay_makes_no_network_requests(self):
        identity = self.package([guidance()])
        with patch('requests.Session.get', side_effect=AssertionError('Network forbidden')):
            result = build_company_evidence_payload(1, package_id=identity, as_of=ENTRY, db_path=self.db)
        self.assertEqual(result['package_id'], identity)

    def test_source_excerpt_round_trips(self):
        source = guidance()
        identity = self.package([source])
        result = build_company_evidence_payload(1, package_id=identity, as_of=ENTRY, db_path=self.db)
        self.assertEqual(result['facts'][0]['source_excerpt'], source.text)

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

    def test_reverted_guidance_becomes_current_with_new_observation(self):
        self.package([guidance()])
        self.package([guidance(80, 90, at=ENTRY)], ENTRY)
        source = replace(guidance(), observed_at=END)
        identity = self.package([source], END)
        payload = build_company_evidence_payload(1, package_id=identity, as_of=END, db_path=self.db)
        self.assertEqual([f['value'] for f in payload['facts'] if f['kind'] == 'guidance' and not f['superseded']], [100])

    def test_conflicting_prior_baselines_do_not_produce_derived_change(self):
        sources = [guidance(kind='prior', url='https://issuer.test/a'),
                   guidance(90, 95, kind='prior', url='https://issuer.test/b'),
                   guidance(120, 125, at=END)]
        identity = self.package(sources, END)
        result = build_company_evidence_payload(1, package_id=identity, as_of=END, db_path=self.db)
        self.assertFalse(any(f['kind'] == 'derived' for f in result['facts']))

    def test_staleness_recomputed_without_changing_package(self):
        identity = self.package([guidance()])
        result = build_company_evidence_payload(1, package_id=identity, as_of=END, db_path=self.db)
        self.assertEqual(result['freshness_at_read'], 'stale')

    def test_schedule_changes_create_document_versions(self):
        source = replace(guidance(), scheduled_events=({'scheduled_at': '2026-09-20', 'confirmed': True},))
        first = ingest_source(COMPANY, source, ENTRY, db_path=self.db)
        second = ingest_source(COMPANY, replace(source, scheduled_events=({'scheduled_at': '2026-09-21', 'confirmed': True},)), END, db_path=self.db)
        self.assertNotEqual(first[0], second[0])


class ExtractionTests(unittest.TestCase):
    def test_structured_period_mismatch_rejected(self):
        source = guidance()
        with self.assertRaises(ValueError):
            validate_extracted_fact({**source.facts[0], 'fiscal_period': 'FY2027-Q4'}, source.text, COMPANY)

    def test_structured_basis_mismatch_rejected(self):
        source = guidance(basis='adjusted')
        with self.assertRaises(ValueError):
            validate_extracted_fact({**source.facts[0], 'accounting_basis': 'US-GAAP'}, source.text, COMPANY)

    def test_sec_entity_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            normalize_company_facts({'cik': 999}, COMPANY['cik'], {ACCESSION})

    def test_period_uses_fact_dates_instead_of_filing_quarter(self):
        self.assertEqual(actuals().facts[0]['fiscal_period'], '2026-04-01/2026-06-30')

    def test_unobserved_accession_is_not_extracted(self):
        payload = {'cik': 1045810, 'facts': {'us-gaap': {'Revenues': {'units': {'USD': [
            {'val': 100, 'start': '2026-01-01', 'end': '2026-03-31', 'accn': 'other'}]}}}}}
        self.assertEqual(normalize_company_facts(payload, COMPANY['cik'], {ACCESSION})[1], ())

    def test_guidance_with_missing_basis_is_not_assumed_gaap(self):
        self.assertEqual(guidance(basis='').facts[0]['accounting_basis'], 'unspecified')

    def test_eps_with_missing_basis_is_not_extracted(self):
        self.assertEqual(guidance_facts('Q3 FY2026 EPS guidance is USD $1 to $2.'), ())

    def test_ambiguous_dollar_currency_is_not_assumed_usd(self):
        self.assertEqual(guidance_facts('Q3 FY2026 GAAP revenue guidance is $100 to $110 million.'), ())

    def test_quarter_spelling_normalizes(self):
        self.assertEqual(guidance(period='Q3 fiscal 2026').facts[0]['fiscal_period'], 'FY2026-Q3')

    def test_unknown_fiscal_period_is_not_invented(self):
        self.assertEqual(guidance_facts('Next quarter GAAP revenue guidance is USD $100 to $110 million.'), ())

    def test_number_not_in_source_rejected(self):
        source = guidance()
        with self.assertRaises(ValueError):
            validate_extracted_fact({**source.facts[0], 'value': 999}, source.text, COMPANY)

    def test_structured_entity_mismatch_rejected(self):
        source = guidance()
        with self.assertRaises(ValueError):
            validate_extracted_fact({**source.facts[0], 'company_id': 2}, source.text, COMPANY)

    def test_date_only_schedule_does_not_invent_time(self):
        html = '<script type="application/ld+json">{"@type":"Event","name":"Earnings call","startDate":"2026-10-20"}</script>'
        self.assertEqual(scheduled_events(html)[0]['precision'], 'date')

    def test_cancelled_event_not_confirmed(self):
        html = '<script type="application/ld+json">{"@type":"Event","name":"Earnings call","startDate":"2026-10-20","eventStatus":"https://schema.org/EventCancelled"}</script>'
        self.assertFalse(scheduled_events(html)[0]['confirmed'])


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

    def test_retries_are_counted_against_quota(self):
        self.session.get.side_effect = [self.response(status=429), self.response(status=503), self.response()]
        self.http.get(self.url)
        from contextlib import closing
        with closing(get_connection(self.db)) as conn:
            count = conn.execute('SELECT requests FROM company_provider_usage').fetchone()[0]
        self.assertEqual(count, 3)

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

    def test_timeout_and_no_redirect_are_passed_to_http(self):
        self.session.get.return_value = self.response()
        self.http.get(self.url)
        self.assertEqual(self.session.get.call_args.kwargs['timeout'], self.policy.timeout_seconds)

    def test_outside_host_rejected_before_http(self):
        with self.assertRaises(ValueError):
            self.http.get('https://unconfigured.test/release')

    def test_redirect_is_not_followed(self):
        self.session.get.return_value = self.response(status=302)
        with self.assertRaises(ProviderUnavailable):
            self.http.get(self.url)

    def test_response_size_is_bounded(self):
        self.http.policy = replace(self.policy, max_response_bytes=3)
        self.session.get.return_value = self.response()
        with self.assertRaises(ProviderUnavailable):
            self.http.get(self.url)

    def test_later_response_retains_old_body(self):
        self.session.get.return_value = self.response('original')
        original = self.http.get(self.url)
        self.now += timedelta(days=1)
        self.session.get.return_value = self.response('revision')
        self.http.get(self.url)
        from contextlib import closing
        with closing(get_connection(self.db)) as conn:
            body = conn.execute('SELECT body FROM company_provider_responses WHERE response_id=?', (original['response_id'],)).fetchone()[0]
        self.assertEqual(body, 'original')

    def test_reverted_response_stays_current_after_restart(self):
        self.session.get.side_effect = [self.response('a'), self.response('b'), self.response('a')]
        for _ in range(3):
            self.http.get(self.url)
            self.now += timedelta(seconds=1)
        self.assertEqual(self.http.get(self.url, ttl_seconds=3600)['body'], 'a')


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

    def test_sec_furnished_release_exhibit_is_extracted(self):
        self.http.get.side_effect = self.sec_responses
        result = self.adapter.collect(COMPANY, {})
        self.assertEqual(result.documents[0].facts[0]['kind'], 'guidance')

    def test_sec_and_collector_build_margin_from_verified_actuals(self):
        self.http.get.side_effect = self.sec_responses
        result = collect_company(COMPANY, [self.adapter], self.policy, db_path=self.db,
                                 clock=lambda: datetime.fromisoformat(ENTRY))
        payload = build_company_evidence_payload(1, package_id=result['package_id'], as_of=ENTRY, db_path=self.db)
        self.assertEqual({fact['metric']: fact['value'] for fact in payload['facts'] if fact['kind'] == 'derived'},
                         {'gross_margin': 60, 'operating_margin': 40})

    def test_sec_submissions_issuer_mismatch_rejected(self):
        self.http.get.return_value = self.response('url', {'cik': 999})
        with self.assertRaises(ValueError):
            self.adapter.collect(COMPANY, {})

    def test_issuer_feed_collects_schedule_with_source_identity(self):
        feed = '<rss><channel><item><link>https://issuer.test/release</link><pubDate>Tue, 01 Sep 2026 20:00:00 GMT</pubDate></item></channel></rss>'
        html = '<p>' + guidance().text + '</p><script type="application/ld+json">{"@type":"Event","name":"Earnings call","startDate":"2026-09-20"}</script>'
        self.http.get.side_effect = [self.response('https://issuer.test/feed', feed), self.response('https://issuer.test/release', html)]
        adapter = IssuerReleaseAdapter(self.http, 'https://issuer.test/feed', self.policy)
        result = collect_company(COMPANY, [adapter], self.policy, db_path=self.db,
                                 clock=lambda: datetime.fromisoformat(ENTRY))
        package = get_evidence_package(result['package_id'], db_path=self.db)
        self.assertIn(package['scheduled_events'][0]['document_id'], package['document_ids'])

    def test_optional_consensus_removes_missing_feed_gap(self):
        source = guidance()
        consensus = replace(source, provider='licensed_fixture', facts=({**source.facts[0], 'kind': 'consensus'},))
        adapter = Mock(name='consensus_adapter')
        adapter.name = 'consensus_fixture'
        adapter.collect.return_value = CollectionResult((consensus,), {})
        result = collect_company(COMPANY, [adapter], self.policy, db_path=self.db,
                                 clock=lambda: datetime.fromisoformat(ENTRY))
        self.assertNotIn('consensus_unavailable; market_surprise_unknown',
                         get_evidence_package(result['package_id'], db_path=self.db)['gaps'])


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

    def test_cursor_is_persisted_after_success(self):
        self.collect()
        self.assertEqual(collection_state('fixture', '1', db_path=self.db)['cursor'], {'last': 'release-1'})

    def test_restart_uses_persisted_freshness(self):
        self.collect()
        self.adapter.collect.side_effect = AssertionError('Refetch before due')
        self.assertEqual(self.collect()['providers'], {'fixture': 'cached'})

    def test_failure_preserves_last_successful_cursor(self):
        self.collect()
        self.now += timedelta(days=1)
        self.adapter.collect.side_effect = RuntimeError('provider unavailable')
        self.collect()
        self.assertEqual(collection_state('fixture', '1', db_path=self.db)['cursor'], {'last': 'release-1'})

    def test_provider_failure_visible_in_package(self):
        self.collect()
        self.now += timedelta(days=1)
        self.adapter.collect.side_effect = RuntimeError('provider unavailable')
        result = self.collect()
        self.assertIn('fixture_collection_failed:RuntimeError', get_evidence_package(result['package_id'], db_path=self.db)['gaps'])

    def test_disabled_collector_never_calls_provider(self):
        with patch('requests.Session.get', side_effect=AssertionError('Network forbidden')):
            result = collect_company_evidence(db_path=self.db, policy=self.policy)
        self.assertEqual(result['status'], 'disabled')

    def test_mapping_survives_restart(self):
        update_issuer_mapping(1, 'NVDA', 1045810, 'fixture', ENTRY, db_path=self.db)
        initialize_database(db_path=self.db)
        self.assertEqual(get_issuer_mapping(1, db_path=self.db)['cik'], COMPANY['cik'])

    def test_mapping_rejects_company_symbol_mismatch(self):
        with self.assertRaises(ValueError):
            update_issuer_mapping(1, 'OTHER', 1045810, 'fixture', ENTRY, db_path=self.db)

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
        with sqlite3.connect(legacy) as conn:
            conn.execute('CREATE TABLE companies (id INTEGER PRIMARY KEY)')
        self.assertEqual(build_company_evidence_payload(1, as_of=ENTRY, db_path=legacy)['coverage_status'], 'unavailable')

    def test_saved_package_reaches_both_prompts(self):
        from agent_stages.strategist_prompt import build_strategist_prompt
        from agent_stages.manager_prompt import build_manager_prompt
        package = self.saved_package()
        evidence = build_company_evidence_payload(1, package_id=package, as_of=ENTRY, db_path=self.db)
        payload = {'company': {'company_id': 1, 'symbol': 'NVDA'}, 'company_evidence': evidence}
        prompts = [build_strategist_prompt(payload)[1], build_manager_prompt(payload)[1]]
        self.assertEqual([json.loads(p)['company_evidence']['package_id'] for p in prompts], [package, package])

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

    def test_strategist_builder_attaches_dated_package(self):
        from agent_builders import strategist_payload as builder
        package = self.saved_package()
        evidence = {'company': {}, 'peer_groups': {}, 'macro_impacts': [], 'sector_impacts': [],
                    'industry_impacts': [], 'company_impacts': []}
        def project(company_id, **kwargs):
            return build_company_evidence_payload(company_id, db_path=self.db, **kwargs)
        with patch.object(builder, 'initialize_news_database'), \
             patch.object(builder, 'build_strategist_evidence_sections', return_value=evidence), \
             patch.object(builder, '_build_opportunist_rollup', return_value={}), \
             patch.object(builder, '_build_view', return_value={}), \
             patch.object(builder, '_serialize_company_scope', return_value={'company_id': 1, 'symbol': 'NVDA'}), \
             patch.object(builder, '_build_supporting_articles', return_value={}), \
             patch('agent_builders.company_evidence_payload.build_company_evidence_payload', side_effect=project):
            payload = builder.build_strategist_input('NVDA', end_time=datetime.fromisoformat(ENTRY))
        self.assertEqual(payload['company_evidence']['package_id'], package)


if __name__ == '__main__':
    unittest.main()
