"""Plan D slice D1: content_updated_at, FTS5 indexes with backfill, and search_archive query parsing."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import re
import sqlite3
import unittest

from db_common import forget_setup, get_connection
from market_db_modules.bootstrap import initialize_database as initialize_market
from market_db_modules.company_evidence import record_evidence_document
from news_db_modules.article_store import add_news_article
from news_db_modules.bootstrap import initialize_database as initialize_news
from research_foundation_contracts import EvidenceDocument
from services.research_tools import OPERATIONS, ResearchRequest
from services import research_tools
from tests.db_fixtures import temporary_database
from tests.research_foundation_fixtures import ENTRY, START

EVENT = 'catalyst:earnings-fixture'
HOSTILE_QUERIES = ['"quoted phrase"', 'nvda*', 'NEAR(nvda export)', 'export OR licence', 'export AND NOT licence',
                   'pre-announcement -licence', 'title:export', '"unterminated', 'export" OR "12', '^nvda (export']


def fts_expression(*args):
    # Looked up per call so a missing builder fails these tests, not the whole module.
    return research_tools.fts_expression(*args)


def parse(query, **changes):
    arguments = {'company_id': 1, 'as_of': ENTRY, 'query': query, **changes}
    data = {'question': 'What was reported?', 'operation': 'search_archive', 'arguments': arguments, 'critical': False}
    return ResearchRequest.parse(data, company_id=1, event_id=EVENT, cutoff=ENTRY)


def canonical(query):
    return parse(query).arguments['query']


class ParseTests(unittest.TestCase):
    def setUp(self):
        # Rejections only mean something once a valid query is accepted.
        self.assertEqual(canonical('export licence'), 'export licence')

    def test_operation_is_registered_with_a_query_argument(self):
        self.assertEqual(OPERATIONS['search_archive'], ('query',))

    def test_parse_keeps_the_other_arguments(self):
        request = parse('export licence')
        self.assertEqual((request.operation, request.arguments['company_id'], request.arguments['as_of']),
                         ('search_archive', 1, ENTRY))

    def test_case_order_spacing_and_punctuation_do_not_change_the_key(self):
        self.assertEqual(parse('Export  licence NVDA').key, parse('nvda export licence').key)
        self.assertEqual(parse('Export  licence NVDA').key, parse('NVDA, export; licence!').key)

    def test_canonical_query_is_sorted_unique_terms(self):
        self.assertEqual(canonical('Export  licence NVDA export'), 'export licence nvda')

    def test_different_terms_give_different_keys(self):
        self.assertNotEqual(parse('export licence').key, parse('export rules').key)

    def test_hyphen_and_colon_split_terms(self):
        self.assertEqual(canonical('pre-announcement title:export'), 'announcement export pre title')

    def test_stop_words_are_dropped(self):
        self.assertEqual(canonical('the export of licence'), 'export licence')

    def test_query_of_only_stop_words_is_rejected(self):
        with self.assertRaises(ValueError):
            parse('the of')

    def test_query_length_bounds(self):
        terms = ' '.join(c * 40 for c in 'abcd')
        self.assertEqual(len(parse(terms.ljust(200, '-')).arguments['query'].split()), 4)
        with self.assertRaises(ValueError):
            parse(terms.ljust(201, '-'))
        self.assertEqual(canonical('gpu'), 'gpu')
        for short in ('', 'ab'):
            with self.subTest(short=short), self.assertRaises(ValueError):
                parse(short)

    def test_query_must_be_a_string(self):
        for value in (None, 123, ['export', 'licence'], {'q': 'export'}, b'export licence'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse(value)

    def test_query_without_terms_is_rejected(self):
        for value in ('!!! ???', '--- ***'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse(value)

    def test_term_count_limit_counts_unique_terms(self):
        eight = ' '.join(f'term{i}' for i in range(8))
        self.assertEqual(len(canonical(eight).split()), 8)
        self.assertEqual(len(canonical(eight + ' term0 TERM1').split()), 8)
        with self.assertRaises(ValueError):
            parse(eight + ' term8')

    def test_term_length_bounds(self):
        self.assertEqual(canonical('x' * 40 + ' ab'), 'ab ' + 'x' * 40)
        with self.assertRaises(ValueError):
            parse('x' * 41 + ' ab')
        with self.assertRaises(ValueError):
            parse('export q')  # a one-character term is out of bounds, not silently dropped

    def test_missing_extra_or_wrong_arguments_are_rejected(self):
        data = {'question': 'q', 'operation': 'search_archive', 'critical': False}
        base = {'company_id': 1, 'as_of': ENTRY}
        for arguments in (base, {**base, 'query': 'export licence', 'metric': 'revenue'},
                          {**base, 'event_id': EVENT}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                ResearchRequest.parse({**data, 'arguments': arguments}, company_id=1, event_id=EVENT, cutoff=ENTRY)

    def test_company_and_cutoff_checks_still_apply(self):
        with self.assertRaises(ValueError):
            parse('export licence', company_id=2)
        with self.assertRaises(ValueError):
            parse('export licence', as_of='2030-01-01T00:00:00+00:00')


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.db = temporary_database(self)

    def sql(self, statement, params=()):
        with closing(get_connection(self.db)) as conn, conn:
            return [tuple(row) for row in conn.execute(statement, params).fetchall()]

    def article(self, index, title='Export licence update', summary='Summary text', body='Body text'):
        self.sql('INSERT INTO news_articles(id,source,article_key,title,summary,source_url,first_seen_time)'
                 ' VALUES (?,?,?,?,?,?,?)',
                 (index, 'wire', f'key-{index}', title, summary, f'https://wire.test/{index}', START))
        if body is not None:
            self.sql('INSERT INTO news_article_content(article_id,body) VALUES (?,?)', (index, body))
        return index

    def document(self, name='doc-1', text='Export licence restrictions were disclosed.', company_id=1):
        document = EvidenceDocument(document_id=name, provider='fixture', source_url=f'https://issuer.test/{name}',
                                    publisher='Issuer', document_type='filing', text=text, published_at=START,
                                    first_observed_at=START, ingested_at=START, metadata={'company_id': company_id})
        record_evidence_document(document, db_path=self.db)

    def hits(self, table, match):
        return {row[0] for row in self.sql(f'SELECT rowid FROM {table} WHERE {table} MATCH ?', (match,))}

    def news_hits(self, match):
        return self.hits('news_article_fts', match)

    def updated_at(self, article_id):
        return self.sql('SELECT content_updated_at FROM news_articles WHERE id=?', (article_id,))[0][0]

    def become_older_database(self):
        """Drop what this slice added, then make both bootstraps run again on the next call."""
        triggers = self.sql("SELECT name FROM sqlite_master WHERE type='trigger' "
                            "AND (sql LIKE '%fts%' OR sql LIKE '%content_updated_at%')")
        for (name,) in triggers:
            self.sql(f'DROP TRIGGER {name}')
        self.sql('DROP TABLE IF EXISTS news_article_fts')
        self.sql('DROP TABLE IF EXISTS evidence_document_fts')
        self.sql('ALTER TABLE news_articles DROP COLUMN content_updated_at')
        self.sql('DELETE FROM schema_bootstrap')
        forget_setup(self.db)

    def rerun_bootstraps(self):
        self.sql('DELETE FROM schema_bootstrap')
        forget_setup(self.db)
        initialize_market(db_path=self.db)
        initialize_news(db_path=self.db)


class EnvironmentTests(unittest.TestCase):
    def test_fts5_available(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.execute("CREATE VIRTUAL TABLE probe USING fts5(text, tokenize='porter unicode61')")
            conn.execute("INSERT INTO probe(text) VALUES ('exports and licences')")
            self.assertEqual(conn.execute("SELECT count(*) FROM probe WHERE probe MATCH 'export'").fetchone()[0], 1)


class ContentUpdatedAtTests(DatabaseCase):
    def test_new_article_and_legacy_rows_have_no_content_update(self):
        article = self.article(1)
        self.assertIsNone(self.updated_at(article))

    def test_column_exists_with_null_for_rows_that_predate_it(self):
        self.article(1)
        self.become_older_database()
        self.rerun_bootstraps()
        self.assertIsNone(self.updated_at(1))

    def test_real_title_change_sets_utc_iso_time(self):
        article = self.article(1)
        before = datetime.now(timezone.utc) - timedelta(seconds=2)
        self.sql('UPDATE news_articles SET title=? WHERE id=?', ('Revised headline', article))
        stamp = self.updated_at(article)
        parsed = datetime.fromisoformat(stamp)
        self.assertEqual(parsed.utcoffset(), timedelta(0))
        self.assertTrue(before <= parsed <= datetime.now(timezone.utc) + timedelta(seconds=2))
        self.assertTrue(stamp.endswith('+00:00'))
        self.assertIsNotNone(self.sql('SELECT julianday(content_updated_at) FROM news_articles WHERE id=?', (article,))[0][0])

    def test_real_summary_change_sets_time(self):
        article = self.article(1)
        self.sql('UPDATE news_articles SET summary=? WHERE id=?', ('A different summary', article))
        self.assertIsNotNone(self.updated_at(article))

    def test_summary_set_from_null_counts_as_a_change(self):
        article = self.article(1, summary=None)
        self.sql('UPDATE news_articles SET summary=? WHERE id=?', ('Now there is one', article))
        self.assertIsNotNone(self.updated_at(article))

    def test_rewriting_identical_title_and_summary_leaves_it_null(self):
        article = self.article(1, summary=None)
        self.sql('UPDATE news_articles SET title=title, summary=summary WHERE id=?', (article,))
        self.sql('UPDATE news_articles SET title=?, summary=NULL WHERE id=?', ('Export licence update', article))
        self.assertIsNone(self.updated_at(article))

    def test_other_columns_do_not_count_as_content(self):
        article = self.article(1)
        self.sql('UPDATE news_articles SET section=?, evidence_score=?, published_at=? WHERE id=?',
                 ('markets', .5, START, article))
        self.assertIsNone(self.updated_at(article))

    def test_real_body_change_sets_time_on_the_parent_article(self):
        article = self.article(1)
        self.sql('UPDATE news_article_content SET body=? WHERE article_id=?', ('Corrected body text', article))
        self.assertIsNotNone(self.updated_at(article))

    def test_body_set_from_null_counts_as_a_change(self):
        article = self.article(1, body=None)
        self.sql('INSERT INTO news_article_content(article_id,body) VALUES (?,NULL)', (article,))
        self.sql('UPDATE news_article_content SET body=? WHERE article_id=?', ('First body', article))
        self.assertIsNotNone(self.updated_at(article))

    def test_identical_body_and_raw_json_changes_leave_it_null(self):
        article = self.article(1)
        self.sql('UPDATE news_article_content SET body=body WHERE article_id=?', (article,))
        self.sql('UPDATE news_article_content SET body=?, raw_json=? WHERE article_id=?',
                 ('Body text', '{"changed": true}', article))
        self.assertIsNone(self.updated_at(article))

    def test_change_only_touches_its_own_article(self):
        first, second = self.article(1), self.article(2, title='Another story')
        self.sql('UPDATE news_article_content SET body=? WHERE article_id=?', ('Corrected', first))
        self.assertIsNone(self.updated_at(second))

    def test_rescrape_with_identical_content_does_not_count(self):
        args = ('wire', 'key-9', 'Export licence update', 'https://wire.test/9')
        article = add_news_article(*args, summary='Same summary', body='Same body', db_path=self.db)
        add_news_article(*args, summary='Same summary', body='Same body', db_path=self.db)
        self.assertIsNone(self.updated_at(article))

    def test_rescrape_with_new_body_counts(self):
        args = ('wire', 'key-9', 'Export licence update', 'https://wire.test/9')
        article = add_news_article(*args, summary='Same summary', body='Original body', db_path=self.db)
        add_news_article(*args, summary='Same summary', body='Rewritten body', db_path=self.db)
        self.assertIsNotNone(self.updated_at(article))


class ArticleIndexTests(DatabaseCase):
    def test_inserted_article_is_searchable_by_title_summary_and_body_under_its_id(self):
        article = self.article(5, title='Chipmaker headline', summary='Quarterly digest', body='Licensing restrictions')
        for word in ('chipmaker', 'digest', 'licensing'):
            with self.subTest(word=word):
                self.assertEqual(self.news_hits(word), {article})

    def test_porter_stemming_matches_inflections(self):
        article = self.article(1, body='NVDA exports were restricted')
        self.assertEqual(self.news_hits('export'), {article})
        self.assertEqual(self.news_hits('restricting'), {article})

    def test_article_without_content_row_is_indexed_by_title(self):
        article = self.article(1, title='Headline only story', body=None)
        self.assertEqual(self.news_hits('headline'), {article})

    def test_title_update_moves_the_index_row(self):
        article = self.article(1, title='Original headline')
        self.sql('UPDATE news_articles SET title=? WHERE id=?', ('Revised wording', article))
        self.assertEqual(self.news_hits('original'), set())
        self.assertEqual(self.news_hits('revised'), {article})
        self.assertEqual(self.news_hits('wording'), {article})

    def test_summary_update_moves_the_index_row(self):
        article = self.article(1, summary='Old digest')
        self.sql('UPDATE news_articles SET summary=? WHERE id=?', ('New abstract', article))
        self.assertEqual((self.news_hits('digest'), self.news_hits('abstract')), (set(), {article}))

    def test_body_update_moves_the_index_row_and_keeps_the_title(self):
        article = self.article(1, title='Stable headline', body='Original passage')
        self.sql('UPDATE news_article_content SET body=? WHERE article_id=?', ('Replacement passage', article))
        self.assertEqual(self.news_hits('original'), set())
        self.assertEqual(self.news_hits('replacement'), {article})
        self.assertEqual(self.news_hits('stable'), {article})

    def test_title_update_keeps_the_body(self):
        article = self.article(1, body='Durable passage')
        self.sql('UPDATE news_articles SET title=? WHERE id=?', ('Changed headline', article))
        self.assertEqual(self.news_hits('durable'), {article})

    def test_repeated_edits_leave_one_row_per_article(self):
        article = self.article(1)
        self.article(2, title='Unrelated story')
        for number in range(3):
            self.sql('UPDATE news_articles SET title=? WHERE id=?', (f'Title {number}', article))
            self.sql('UPDATE news_article_content SET body=? WHERE article_id=?', (f'Body {number}', article))
        self.assertEqual(self.sql('SELECT count(*) FROM news_article_fts')[0][0], 2)
        self.assertEqual(self.news_hits('title'), {article})

    def test_rescrape_updates_the_index(self):
        args = ('wire', 'key-9', 'Export licence update', 'https://wire.test/9')
        article = add_news_article(*args, body='Original passage', db_path=self.db)
        add_news_article(*args, body='Rewritten passage', db_path=self.db)
        self.assertEqual((self.news_hits('original'), self.news_hits('rewritten')), (set(), {article}))

    def test_deleting_an_article_removes_its_index_row(self):
        gone, kept = self.article(1, title='Vanishing zebra story'), self.article(2, title='Lasting story')
        self.assertEqual(self.news_hits('zebra'), {gone})
        self.sql('DELETE FROM news_articles WHERE id=?', (gone,))
        self.assertEqual(self.news_hits('zebra'), set())
        self.assertEqual(self.sql('SELECT rowid FROM news_article_fts'), [(kept,)])
        self.assertEqual(self.sql('SELECT count(*) FROM news_article_content WHERE article_id=?', (gone,))[0][0], 0)

    def test_backfill_indexes_articles_that_predate_the_index(self):
        first = self.article(1, title='Alpha headline', body='Alpha passage')
        second = self.article(2, title='Beta headline', body=None)
        self.become_older_database()
        self.assertEqual(self.sql("SELECT count(*) FROM sqlite_master WHERE name='news_article_fts'")[0][0], 0)
        self.rerun_bootstraps()
        self.assertEqual(self.news_hits('headline'), {first, second})
        self.assertEqual(self.news_hits('passage'), {first})

    def test_backfill_runs_once_and_never_duplicates(self):
        self.article(1)
        self.article(2, title='Second story')
        self.become_older_database()
        self.rerun_bootstraps()
        self.rerun_bootstraps()
        self.assertEqual(self.sql('SELECT count(*) FROM news_article_fts')[0][0], 2)

    def test_backfilled_database_keeps_following_edits(self):
        article = self.article(1, title='Original headline')
        self.become_older_database()
        self.rerun_bootstraps()
        self.sql('UPDATE news_articles SET title=? WHERE id=?', ('Revised wording', article))
        self.assertEqual((self.news_hits('original'), self.news_hits('revised')), (set(), {article}))
        self.assertIsNotNone(self.updated_at(article))


class DocumentIndexTests(DatabaseCase):
    def test_recorded_document_is_searchable_with_its_company(self):
        self.document('doc-1', company_id=7)
        rows = self.sql("SELECT document_id, company_id FROM evidence_document_fts "
                        "WHERE evidence_document_fts MATCH 'licence'")
        self.assertEqual(rows, [('doc-1', 7)])

    def test_search_uses_the_document_text_only(self):
        self.document('doc-1')
        self.assertEqual(self.sql("SELECT count(*) FROM evidence_document_fts WHERE evidence_document_fts MATCH 'doc'")[0][0], 0)

    def test_recording_the_same_document_twice_indexes_it_once(self):
        self.document('doc-1')
        self.document('doc-1')
        self.assertEqual(self.sql('SELECT count(*) FROM evidence_document_fts')[0][0], 1)

    def test_each_document_gets_its_own_row(self):
        self.document('doc-1', text='Export licence restrictions.', company_id=1)
        self.document('doc-2', text='Dividend declared.', company_id=2)
        rows = self.sql("SELECT document_id, company_id FROM evidence_document_fts "
                        "WHERE evidence_document_fts MATCH 'dividend'")
        self.assertEqual(rows, [('doc-2', 2)])

    def test_backfill_indexes_documents_that_predate_the_index(self):
        self.document('doc-1', company_id=1)
        self.document('doc-2', text='Dividend declared.', company_id=2)
        self.become_older_database()
        self.assertEqual(self.sql("SELECT count(*) FROM sqlite_master WHERE name='evidence_document_fts'")[0][0], 0)
        self.rerun_bootstraps()
        self.rerun_bootstraps()
        self.assertEqual(self.sql('SELECT document_id, company_id FROM evidence_document_fts ORDER BY document_id'),
                         [('doc-1', 1), ('doc-2', 2)])
        self.assertEqual(self.sql("SELECT document_id FROM evidence_document_fts "
                                  "WHERE evidence_document_fts MATCH 'dividend'"), [('doc-2',)])


class ExpressionTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        self.article(1, title='Export licence update', body='NEAR the border')
        self.article(2, title='Export rules', body='Nothing else')
        self.article(3, title='Earnings preview', body='Pre-announcement and NOT a surprise')
        self.document('doc-1', text='Export licence restrictions.')

    def test_and_form_requires_every_term(self):
        self.assertEqual(fts_expression('export licence'), '"export" AND "licence"')
        self.assertEqual(self.news_hits(fts_expression('export licence')), {1})

    def test_or_form_matches_any_term(self):
        self.assertEqual(fts_expression('export licence', 'OR'), '"export" OR "licence"')
        self.assertEqual(self.news_hits(fts_expression('export licence', 'OR')), {1, 2})

    def test_single_term_has_no_operator(self):
        self.assertEqual(fts_expression('export'), '"export"')

    def test_expression_runs_against_the_document_index(self):
        match = fts_expression(canonical('Export licence'))
        self.assertEqual(self.hits('evidence_document_fts', match), {1})

    def test_operator_words_in_the_query_are_ordinary_terms(self):
        expression = fts_expression(canonical('near border'))
        self.assertIn('"near"', expression)
        self.assertEqual(self.news_hits(expression), {1})

    def test_hostile_queries_become_quoted_terms_and_match_without_error(self):
        pattern = re.compile(r'^"[a-z0-9]+"( (AND|OR) "[a-z0-9]+")*$')
        for query in HOSTILE_QUERIES:
            for operator in ('AND', 'OR'):
                with self.subTest(query=query, operator=operator):
                    expression = fts_expression(canonical(query), operator)
                    self.assertRegex(expression, pattern)
                    self.news_hits(expression)
                    self.hits('evidence_document_fts', expression)

    def test_unrecognised_operator_is_rejected(self):
        with self.assertRaises(ValueError):
            fts_expression('export licence', 'NEAR')


if __name__ == '__main__':
    unittest.main()
