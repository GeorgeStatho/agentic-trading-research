"""Plan D slice D2: search_archive execution, article snapshots, planner contract, flag and Phase 4 citations."""
from contextlib import closing
import importlib
import os
import unittest
from unittest.mock import patch

from db_common import get_connection
from market_db_modules.company_evidence import get_evidence_document, get_evidence_package, record_evidence_document
from research_foundation_contracts import EvidenceDocument
from services.evidence_research_policy import EvidenceResearchPolicy
from services.evidence_thesis_checks import build_thesis_sources, check_claim
from services.research_tools import ResearchTools
from tests.research_foundation_fixtures import ENTRY, START
from tests.test_event_discovery import later
from tests.test_research_orchestrator import FakePlanner, ResearchFixture

AFTER_CUTOFF = later(3600, at=ENTRY)
BEFORE_CUTOFF = later(-3600, at=ENTRY)


def research_search():
    # Looked up per call so a missing module fails these tests, not the whole file.
    return importlib.import_module('services.research_search')


class SearchFixture(ResearchFixture):
    def setUp(self):
        super().setUp()
        self.event_id = self.tools.event_id

    def news(self, article_id, title, body='Body text', *, first_seen=START, published=START, company_id=1,
             content_updated_at=None):
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute('INSERT INTO news_articles(id,source,article_key,title,summary,source_url,published_at,'
                         'first_seen_time) VALUES (?,?,?,?,?,?,?,?)',
                         (article_id, 'wire', f'search-{article_id}', title, 'Summary text',
                          f'https://wire.test/{article_id}', published, first_seen))
            conn.execute('INSERT INTO news_article_content(article_id,body) VALUES (?,?)', (article_id, body))
            if company_id:
                conn.execute('INSERT INTO company_news_articles(company_id,article_id) VALUES (?,?)',
                             (company_id, article_id))
            if content_updated_at:
                conn.execute('UPDATE news_articles SET content_updated_at=? WHERE id=?', (content_updated_at, article_id))
        return article_id

    def document(self, name, text, *, company_id=1, at=START):
        record_evidence_document(EvidenceDocument(
            document_id=name, provider='fixture', source_url=f'https://issuer.test/{name}', publisher='Issuer',
            document_type='filing', text=text, published_at=at, first_observed_at=at, ingested_at=at,
            metadata={'company_id': company_id}), db_path=self.db)
        return name

    def search_tools(self, enabled=True):
        return ResearchTools(company_id=1, event_id=self.event_id, cutoff=ENTRY, db_path=self.db,
                             search_enabled=enabled)

    def search_request(self, query):
        return {'question': 'What was reported about export licences?', 'operation': 'search_archive',
                'arguments': {'company_id': 1, 'as_of': ENTRY, 'query': query}, 'critical': False}

    def search(self, query):
        tools = self.search_tools()
        return tools.execute(tools.parse(self.search_request(query)), timeout=30)

    def article_ids(self, result):
        return {d['metadata']['article_id'] for d in result['documents'] if d['provider'] == 'news_archive'}

    def document_ids(self, result):
        return {d['document_id'] for d in result['documents'] if d['provider'] != 'news_archive'}


class PointInTimeTests(SearchFixture):
    def test_article_first_seen_after_the_cutoff_is_excluded(self):
        self.news(101, 'Export licence granted', first_seen=AFTER_CUTOFF, published=AFTER_CUTOFF)
        self.news(102, 'Export licence pending')
        self.assertEqual(self.article_ids(self.search('export licence')), {102})

    def test_article_whose_content_changed_after_the_cutoff_is_excluded(self):
        self.news(101, 'Export licence granted', content_updated_at=AFTER_CUTOFF)
        self.assertEqual(self.article_ids(self.search('export licence')), set())

    def test_article_changed_before_the_cutoff_or_never_changed_is_included(self):
        self.news(101, 'Export licence granted', content_updated_at=BEFORE_CUTOFF)
        self.news(102, 'Export licence pending')
        self.assertEqual(self.article_ids(self.search('export licence')), {101, 102})

    def test_document_ingested_after_the_cutoff_is_excluded(self):
        self.document('doc-late', 'Export licence disclosed late.', at=AFTER_CUTOFF)
        self.document('doc-early', 'Export licence disclosed early.')
        self.assertEqual(self.document_ids(self.search('export licence')), {'doc-early'})


class ScopeAndResultTests(SearchFixture):
    def test_article_linked_only_to_another_company_is_excluded(self):
        self.news(101, 'Export licence granted', company_id=2)
        self.news(102, 'Export licence pending', company_id=None)
        self.news(103, 'Export licence reviewed')
        self.assertEqual(self.article_ids(self.search('export licence')), {103})

    def test_document_of_another_company_is_excluded(self):
        self.document('doc-other', 'Export licence for another issuer.', company_id=2)
        self.document('doc-own', 'Export licence for this issuer.')
        self.assertEqual(self.document_ids(self.search('export licence')), {'doc-own'})

    def test_both_sources_are_interleaved_and_capped_at_eight(self):
        for index in range(6):
            self.news(110 + index, f'Export licence update {index}')
            self.document(f'doc-{index}', f'Export licence filing {index}.')
        result = self.search('export licence')
        self.assertEqual((len(self.article_ids(result)), len(self.document_ids(result))), (4, 4))
        self.assertTrue(result['search']['more_results_available'])

    def test_overflow_is_reported_without_a_gap(self):
        for index in range(10):
            self.news(110 + index, f'Export licence update {index}')
        result = self.search('export licence')
        self.assertEqual(len(result['documents']), 8)
        self.assertTrue(result['search']['more_results_available'])
        self.assertEqual(result['gaps'], [])
        self.assertTrue(result['available'])

    def test_results_that_fit_report_no_more_results(self):
        self.news(101, 'Export licence granted')
        self.assertFalse(self.search('export licence')['search']['more_results_available'])

    def test_zero_hits_give_search_no_results_and_never_a_truncation_gap(self):
        result = self.search('zebra quarantine')
        self.assertIn('search_no_results', result['gaps'])
        self.assertFalse(result['available'])
        self.assertFalse(any('truncated' in gap for gap in result['gaps']))

    def test_all_terms_are_required_before_falling_back_to_any_term(self):
        self.news(101, 'Export licence granted')
        self.news(102, 'Export rules tightened')
        both = self.search('export licence')
        self.assertEqual((self.article_ids(both), both['search']['mode']), ({101}, 'all_terms'))
        fallback = self.search('export zebra')
        self.assertEqual((self.article_ids(fallback), fallback['search']['mode']), ({101, 102}, 'any_term'))

    def test_hostile_queries_run_without_error(self):
        self.news(101, 'Export licence granted')
        for query in ('"export* NEAR(licence)', 'export OR licence -rules', 'title:export ^licence'):
            with self.subTest(query=query):
                self.assertIn(101, self.article_ids(self.search(query)))

    def test_facts_from_matching_documents_are_returned(self):
        # The fixture package holds a guidance release with extracted revenue facts.
        result = self.search('revenue guidance million')
        self.assertTrue(result['facts'])
        hit_documents = {d['document_id'] for d in result['documents']}
        self.assertTrue(all(f['document_id'] in hit_documents for f in result['facts']))

    def test_articles_are_not_counted_twice_once_snapshotted(self):
        self.news(101, 'Export licence granted')
        self.search('export licence')
        result = self.search('export licence')
        self.assertEqual(len(result['documents']), 1)


class SnapshotTests(SearchFixture):
    def snapshot(self, article_id):
        return research_search().snapshot_article(article_id, 1, db_path=self.db)

    def test_snapshot_is_an_immutable_news_document(self):
        self.news(101, 'Export licence granted', body='Officials granted the licence on Monday.')
        document = get_evidence_document(self.snapshot(101), db_path=self.db)
        self.assertTrue(document['document_id'].startswith('news:101:'))
        self.assertEqual((document['provider'], document['document_type'], document['publisher']),
                         ('news_archive', 'news_article', 'wire'))
        self.assertEqual(document['text'], 'Export licence granted\n\nSummary text\n\nOfficials granted the licence on Monday.')
        self.assertEqual((document['metadata']['company_id'], document['metadata']['article_id']), (1, 101))
        self.assertEqual(document['source_url'], 'https://wire.test/101')

    def test_snapshot_is_idempotent(self):
        self.news(101, 'Export licence granted')
        first = self.snapshot(101)
        self.assertEqual(self.snapshot(101), first)
        with closing(get_connection(self.db)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM evidence_documents WHERE document_id LIKE 'news:101:%'").fetchone()[0]
        self.assertEqual(count, 1)

    def test_changed_text_gives_a_new_document(self):
        self.news(101, 'Export licence granted')
        first = self.snapshot(101)
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE news_article_content SET body='Licence revoked.' WHERE article_id=101")
        second = self.snapshot(101)
        self.assertNotEqual(first, second)
        self.assertEqual(get_evidence_document(first, db_path=self.db)['text'].split('\n\n')[-1], 'Body text')

    def test_publication_after_first_sight_is_clamped(self):
        self.news(101, 'Export licence granted', published=later(600, at=START))
        document = get_evidence_document(self.snapshot(101), db_path=self.db)
        self.assertEqual(document['published_at'], START)
        self.assertTrue(document['metadata']['published_at_clamped'])

    def test_ingestion_time_covers_the_last_content_change(self):
        self.news(101, 'Export licence granted', content_updated_at=BEFORE_CUTOFF)
        document = get_evidence_document(self.snapshot(101), db_path=self.db)
        self.assertEqual((document['first_observed_at'], document['ingested_at']), (START, BEFORE_CUTOFF))


    def test_sqlite_style_naive_times_are_read_as_utc(self):
        # first_seen_time and created_at default to SQLite CURRENT_TIMESTAMP, which has no offset.
        self.news(101, 'Export licence granted', first_seen='2026-09-01 20:00:00', published='2026-09-01T21:00:00Z')
        document = get_evidence_document(self.snapshot(101), db_path=self.db)
        self.assertEqual((document['first_observed_at'], document['published_at']), (START, START))
        self.assertIn(101, self.article_ids(self.search('export licence')))


class FlagTests(SearchFixture):
    def test_the_policy_defaults_off_and_reads_the_environment(self):
        self.assertFalse(EvidenceResearchPolicy().search_enabled)
        with patch.dict(os.environ, {'RESEARCH_SEARCH_ENABLED': 'true'}):
            self.assertTrue(EvidenceResearchPolicy.from_env().search_enabled)
        with patch.dict(os.environ, {'RESEARCH_SEARCH_ENABLED': 'false'}):
            self.assertFalse(EvidenceResearchPolicy.from_env().search_enabled)

    def test_disabled_tools_reject_the_operation(self):
        with self.assertRaises(ValueError):
            self.search_tools(enabled=False).parse(self.search_request('export licence'))

    def test_disabled_research_hides_the_operation_from_the_planner(self):
        self.run_research()
        self.assertNotIn('search_archive', self.planner.inputs[0]['operations'])

    def test_enabled_research_offers_the_operation_to_the_planner(self):
        self.run_research(policy=EvidenceResearchPolicy(mode='shadow', search_enabled=True))
        self.assertIn('search_archive', self.planner.inputs[0]['operations'])


class PlannerContractTests(unittest.TestCase):
    def test_request_schema_accepts_a_query_and_the_prompt_describes_search(self):
        from agent_stages import research_planner
        arguments = research_planner.REQUEST_SCHEMA['properties']['arguments']['properties']
        self.assertEqual(arguments['query'], {'type': 'string'})
        self.assertIn('search_archive', research_planner.SYSTEM_PROMPT)
        self.assertEqual(research_planner.PROMPT_VERSION, 'bounded-research-planner-v3-search')


class SessionTests(SearchFixture):
    def research_with_search(self, query='export licence'):
        self.prior()
        planner = FakePlanner([{'outcome': 'continue', 'reason': 'Look for context',
                                'requests': [self.search_request(query)]},
                               {'outcome': 'ready', 'reason': 'Enough evidence', 'requests': []}])
        return self.run_research(planner=planner,
                                 policy=EvidenceResearchPolicy(mode='shadow', search_enabled=True))

    def test_overflowing_search_still_lets_a_session_become_ready(self):
        for index in range(10):
            self.news(110 + index, f'Export licence update {index}')
        result = self.research_with_search()
        self.assertEqual(result['status'], 'ready')
        steps = [s for s in self.trace(result)['steps'] if s['kind'] == 'retrieval_result'
                 and s['request']['operation'] == 'search_archive']
        self.assertEqual(len(steps), 1)
        self.assertTrue(steps[0]['result']['search']['more_results_available'])

    def test_search_counts_against_the_retrieval_budget(self):
        self.news(101, 'Export licence granted')
        result = self.research_with_search()
        steps = [s for s in self.trace(result)['steps'] if s['kind'] == 'retrieval_result']
        self.assertEqual(result['retrieval_calls'], len(steps))
        self.assertIn('search_archive', {s['request']['operation'] for s in steps})

    def test_snapshots_enter_the_final_package(self):
        self.news(101, 'Export licence granted')
        result = self.research_with_search()
        package = get_evidence_package(result['final_package_id'], db_path=self.db)
        self.assertTrue(any(identity.startswith('news:101:') for identity in package['document_ids']))

    def test_a_thesis_claim_quoting_a_snapshot_verifies(self):
        self.news(101, 'Export licence granted', body='Officials granted the export licence on Monday.')
        package_id = self.research_with_search()['final_package_id']
        document_id = next(i for i in get_evidence_package(package_id, db_path=self.db)['document_ids']
                           if i.startswith('news:101:'))
        sources = build_thesis_sources({'company': {'company_id': 1},
                                        'company_evidence': {'package_id': package_id}}, db_path=self.db)
        claim = {'claim_id': 'c1', 'claim_type': 'observed_fact', 'central': True, 'statement': 'The licence was granted.',
                 'fact_ids': [], 'document_ids': [document_id], 'article_ids': [], 'fiscal_period': None,
                 'quotes': [{'source_id': document_id, 'text': 'Officials granted the export licence'}]}
        self.assertEqual(check_claim(claim, 'evidence', sources)['status'], 'verified')


if __name__ == '__main__':
    unittest.main()
