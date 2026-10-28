from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import unittest

import catalyst_models
from catalyst_models import CATALYST_FAMILIES, CATALYST_TYPES, CLASSIFICATION_VERSION, classify_catalyst
from db_common import get_connection
from market_db_modules.pricing_research import register_response_events
from news_db_modules.catalysts import attach_candidate_catalysts, build_catalyst_state, refresh_catalyst, store_catalyst_state
from news_db_modules.entity_articles import add_company_news_article

from tests.db_fixtures import temporary_database

GOLDEN = Path(__file__).parent / 'fixtures' / 'catalyst_classification_golden.json'
NOW = datetime.now(timezone.utc)
FIRST_SEEN = NOW - timedelta(hours=47)
PUBLISHED = NOW - timedelta(days=2)
LABELED_AT = NOW - timedelta(hours=24)
RULE_FAILURE = 'Nvidia shares jump as investors cheer AI demand'   # no rule matches: other / unknown
LABEL_V1 = 'catalyst-llm-v1'


def labels():
    # Imported on use so the module-independent tests stay runnable while this module is new.
    from news_db_modules import catalyst_labels
    return catalyst_labels


def label(kind='regulatory', direction='positive', confidence='high'):
    return {'catalyst_type': kind, 'direction': direction, 'confidence': confidence}


def anchor(title, summary=''):
    when = PUBLISHED.isoformat()
    return {'id': 1, 'title': title, 'summary': summary, 'event_time': when, 'first_seen_time': when,
            'primary_ticker': 'NVDA'}


class GoldenClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(GOLDEN.read_text(encoding='utf-8'))

    def test_classification_output_is_unchanged(self):
        for case in self.cases:
            with self.subTest(title=case['title'], summary=case['summary']):
                result = classify_catalyst(case['title'], case['summary'])
                self.assertEqual(result, case['expected'])
                self.assertEqual(list(result), list(case['expected']))

    def test_corpus_exercises_every_type_and_family(self):
        self.assertEqual({case['expected']['catalyst_type'] for case in self.cases}, set(CATALYST_TYPES))
        self.assertEqual({case['expected']['catalyst_family'] for case in self.cases}, set(CATALYST_FAMILIES))

    def test_classify_is_derive_of_detect(self):
        for case in self.cases:
            with self.subTest(title=case['title'], summary=case['summary']):
                detected = catalyst_models.detect_catalyst(case['title'], case['summary'])
                self.assertEqual(catalyst_models.derive_classification(*detected),
                                 classify_catalyst(case['title'], case['summary']))

    def test_label_version_constants(self):
        self.assertEqual(catalyst_models.LABEL_VERSION, 'catalyst-llm-v1')
        self.assertEqual(catalyst_models.LABELED_CLASSIFICATION_VERSION, CLASSIFICATION_VERSION + '+catalyst-llm-v1')


class MergeRuleTests(unittest.TestCase):
    def build(self, title, model_label=None, summary=''):
        return build_catalyst_state('catalyst:merge', [anchor(title, summary)], ticker='NVDA', as_of=NOW.isoformat(),
                                    label=model_label)

    def test_unlabelled_state_is_rules_only(self):
        state = self.build(RULE_FAILURE)
        self.assertEqual((state['classification_source'], state['classification_version']), ('rules', CLASSIFICATION_VERSION))
        self.assertEqual(state['classification_status'], 'needs_review')

    def test_label_fills_a_rule_failure(self):
        state = self.build(RULE_FAILURE, label('regulatory', 'positive'))
        self.assertEqual((state['catalyst_type'], state['direction'], state['catalyst_family'], state['classification_status']),
                         ('regulatory', 'positive', 'regulatory_positive', 'classified'))
        self.assertEqual(state['classification_source'], 'rules+model')
        self.assertEqual(state['classification_version'], catalyst_models.LABELED_CLASSIFICATION_VERSION)

    def test_label_kind_is_used_when_rules_find_none(self):
        state = self.build(RULE_FAILURE, label('legal', 'negative'))
        self.assertEqual((state['catalyst_type'], state['catalyst_family']), ('legal', 'legal_negative'))
        self.assertEqual(state['mechanism'], classify_catalyst('court ruling')['mechanism'])

    def test_rule_kind_with_label_direction_gives_combined_family(self):
        for title in ('Apple quarterly results show iPhone sales steady', 'Tesla EPS beats and misses in the same quarter'):
            with self.subTest(title=title):
                rules = self.build(title)
                self.assertEqual((rules['catalyst_type'], rules['catalyst_family']), ('earnings', 'other'))
                state = self.build(title, label('earnings', 'negative'))
                self.assertEqual((state['catalyst_type'], state['direction'], state['catalyst_family']),
                                 ('earnings', 'negative', 'earnings_negative'))
                self.assertEqual(state['classification_source'], 'rules+model')

    def test_label_of_another_kind_is_ignored_when_rules_found_one(self):
        title = 'Apple quarterly results show iPhone sales steady'
        state = self.build(title, label('legal', 'negative'))
        self.assertEqual(state, self.build(title))
        self.assertEqual((state['catalyst_family'], state['classification_source']), ('other', 'rules'))

    def test_label_of_a_kind_in_the_same_family_applies_its_direction(self):
        title = 'Nvidia launches new chip for data centers'      # product, direction unknown
        state = self.build(title, label('customer_contract', 'positive'))
        self.assertEqual((state['catalyst_type'], state['direction'], state['catalyst_family']),
                         ('product', 'positive', 'product_commercial'))
        self.assertEqual(state['classification_source'], 'rules+model')

    def test_rule_classified_catalyst_ignores_labels(self):
        for title in ('Nvidia raises revenue guidance after strong demand',   # guidance_raise
                      'Nvidia beats earnings estimates on record data center revenue'):   # earnings_positive
            with self.subTest(title=title):
                state = self.build(title, label('legal', 'negative'))
                rules = self.build(title)
                self.assertEqual(state, rules)
                self.assertEqual(state['classification_source'], 'rules')
                self.assertEqual(state['classification_version'], CLASSIFICATION_VERSION)

    def test_classified_kind_with_unknown_direction_takes_label_direction(self):
        state = self.build('Microsoft to acquire gaming studio in $10 billion deal', label('ma', 'negative'))
        self.assertEqual((state['catalyst_type'], state['direction'], state['catalyst_family'], state['classification_status']),
                         ('ma', 'negative', 'ma', 'classified'))
        self.assertEqual(state['catalyst_subtype'], classify_catalyst('Microsoft to acquire rival after loss')['catalyst_subtype'])
        self.assertEqual(state['classification_source'], 'rules+model')

    def test_classified_kind_ignores_the_direction_of_a_label_for_another_event(self):
        title = 'Microsoft to acquire gaming studio in $10 billion deal'
        state = self.build(title, label('earnings', 'positive'))
        self.assertEqual(state, self.build(title))
        self.assertEqual((state['direction'], state['classification_source']), ('unknown', 'rules'))

    def test_medium_confidence_label_is_applied(self):
        state = self.build(RULE_FAILURE, label('regulatory', 'positive', confidence='medium'))
        self.assertEqual((state['catalyst_family'], state['classification_source']), ('regulatory_positive', 'rules+model'))

    def test_low_confidence_label_is_not_applied(self):
        state = self.build(RULE_FAILURE, label('regulatory', 'positive', confidence='low'))
        self.assertEqual((state['catalyst_type'], state['catalyst_family'], state['classification_status']),
                         ('other', 'other', 'needs_review'))
        self.assertEqual((state['classification_source'], state['classification_version']), ('rules', CLASSIFICATION_VERSION))


class CatalystLabelDatabaseTests(unittest.TestCase):
    def setUp(self):
        temporary_database(self)
        with closing(get_connection()) as conn, conn:
            conn.execute("INSERT INTO sectors(id, sector_key, name) VALUES (1, 'technology', 'Technology')")
            conn.execute("INSERT INTO industries(id, sector_id, industry_key, name) VALUES (1, 1, 'chips', 'Chips')")
            conn.execute("INSERT INTO companies(id, industry_id, symbol, name) VALUES (1, 1, 'NVDA', 'Nvidia')")

    def article(self, number, title=RULE_FAILURE, domain='reuters.com', published_at=PUBLISHED):
        article_id = add_company_news_article(company_id=1, source=domain, article_key=str(number), title=title,
            body='Reuters reports the move.', source_url=f'https://{domain}/story/{number}',
            published_at=published_at.isoformat(), recency_score=1, source_reputation_score=.95,
            directness_score=.95, factuality_score=.95)
        with closing(get_connection()) as conn, conn:
            conn.execute('UPDATE news_articles SET first_seen_time=? WHERE id=?', (FIRST_SEEN.isoformat(), article_id))
            return article_id, conn.execute('SELECT catalyst_id FROM news_articles WHERE id=?', (article_id,)).fetchone()[0]

    def record(self, catalyst_id, version=LABEL_V1, labeled_at=LABELED_AT, kind='regulatory', direction='positive',
               confidence='high'):
        with closing(get_connection()) as conn, conn:
            labels().record_catalyst_label(catalyst_id, label_version=version, catalyst_type=kind, direction=direction,
                confidence=confidence, anchor_article_id=1, model='test-model', answered_by={'model': 'test-model'},
                labeled_at=labeled_at.isoformat(), conn=conn)

    def stored(self, catalyst_id):
        with closing(get_connection()) as conn:
            row = conn.execute('SELECT * FROM catalysts WHERE catalyst_id=?', (catalyst_id,)).fetchone()
        return dict(row), json.loads(row['state_json'])

    def latest(self, catalyst_id, **kwargs):
        with closing(get_connection()) as conn:
            return labels().latest_catalyst_label(catalyst_id, conn=conn, **kwargs)

    def pending(self, limit, version):
        with closing(get_connection()) as conn:
            return [row['catalyst_id'] for row in labels().pending_catalysts_for_labeling(limit, version, conn=conn)]

    def refresh(self, catalyst_id):
        with closing(get_connection()) as conn, conn:
            refresh_catalyst(catalyst_id, conn=conn)

    def test_recorded_label_is_returned_as_latest(self):
        _, catalyst = self.article(1)
        self.assertIsNone(self.latest(catalyst))
        self.record(catalyst, 'v1', kind='legal', direction='negative')
        found = self.latest(catalyst)
        self.assertEqual((found['label_version'], found['catalyst_type'], found['direction'], found['confidence']),
                         ('v1', 'legal', 'negative', 'high'))
        self.assertEqual(found['labeled_at'], LABELED_AT.isoformat())

    def test_latest_label_honours_as_of_and_picks_by_labeled_at(self):
        _, catalyst = self.article(1)
        first, second, third = (NOW - timedelta(hours=hours) for hours in (30, 20, 10))
        # Inserted out of time order, with names whose sort order differs from their time order.
        self.record(catalyst, 'z-second', second, kind='legal', direction='negative')
        self.record(catalyst, 'm-third', third, kind='product', direction='positive')
        self.record(catalyst, 'a-first', first, kind='regulatory', direction='positive')
        self.assertEqual(self.latest(catalyst)['label_version'], 'm-third')
        self.assertEqual(self.latest(catalyst, as_of=(NOW - timedelta(hours=15)).isoformat())['label_version'], 'z-second')
        self.assertEqual(self.latest(catalyst, as_of=second.isoformat())['label_version'], 'z-second')   # inclusive
        self.assertEqual(self.latest(catalyst, as_of=(NOW - timedelta(hours=25)).isoformat())['label_version'], 'a-first')
        self.assertIsNone(self.latest(catalyst, as_of=(NOW - timedelta(hours=35)).isoformat()))

    def test_unlabelled_catalyst_state_keeps_rules_source(self):
        _, catalyst = self.article(1)
        row, state = self.stored(catalyst)
        self.assertEqual((state['classification_source'], row['classification_version']), ('rules', CLASSIFICATION_VERSION))
        self.assertEqual(row['classification_status'], 'needs_review')

    def test_label_survives_refresh_after_a_new_article_joins(self):
        _, catalyst = self.article(1)
        self.record(catalyst, kind='regulatory', direction='positive')
        _, joined = self.article(2, domain='cnbc.com')
        self.assertEqual(joined, catalyst)
        row, state = self.stored(catalyst)
        self.assertEqual(state['article_count'], 2)
        self.assertEqual((row['catalyst_type'], row['catalyst_family'], row['classification_status']),
                         ('regulatory', 'regulatory_positive', 'classified'))
        self.assertEqual((state['classification_source'], state['classification_version']),
                         ('rules+model', catalyst_models.LABELED_CLASSIFICATION_VERSION))
        self.assertEqual(row['classification_version'], catalyst_models.LABELED_CLASSIFICATION_VERSION)

    def test_refresh_reads_label_recorded_after_it_was_stamped(self):
        _, catalyst = self.article(1)
        self.record(catalyst, labeled_at=NOW + timedelta(hours=5))   # refresh has no cutoff
        self.refresh(catalyst)
        self.assertEqual(self.stored(catalyst)[1]['classification_source'], 'rules+model')

    def test_low_confidence_label_is_stored_but_not_applied(self):
        _, catalyst = self.article(1)
        self.record(catalyst, confidence='low')
        self.refresh(catalyst)
        row, state = self.stored(catalyst)
        self.assertEqual(self.latest(catalyst)['confidence'], 'low')
        self.assertEqual((row['classification_status'], state['classification_source']), ('needs_review', 'rules'))
        self.assertEqual(row['classification_version'], CLASSIFICATION_VERSION)

    def test_rule_classified_catalyst_ignores_a_stored_label(self):
        _, catalyst = self.article(1, title='Nvidia raises revenue guidance after strong demand')
        self.record(catalyst, kind='legal', direction='negative')
        self.refresh(catalyst)
        row, state = self.stored(catalyst)
        self.assertEqual((row['catalyst_family'], state['classification_source']), ('guidance_raise', 'rules'))

    def attach(self, catalyst, cutoff):
        with closing(get_connection()) as conn, conn:
            article_id = conn.execute('SELECT id FROM news_articles WHERE catalyst_id=?', (catalyst,)).fetchone()[0]
            conn.execute('INSERT INTO candidates (candidate_id, symbol, created_at, catalyst_ids_json, article_ids_json, '
                         'pipeline_version_json) VALUES (?, ?, ?, ?, ?, ?)', ('cand-1', 'NVDA', cutoff, '[]', '[]', '{}'))
            states = attach_candidate_catalysts('cand-1', 'NVDA', [{'article_id': article_id}], cutoff=cutoff, conn=conn)
        return states[0]

    def test_candidate_cutoff_before_the_label_ignores_it(self):
        _, catalyst = self.article(1)
        self.record(catalyst)
        state = self.attach(catalyst, (LABELED_AT - timedelta(hours=12)).isoformat())
        self.assertEqual((state['catalyst_family'], state['classification_source']), ('other', 'rules'))
        self.assertEqual(state['classification_version'], CLASSIFICATION_VERSION)

    def test_candidate_cutoff_after_the_label_uses_it(self):
        _, catalyst = self.article(1)
        self.record(catalyst)
        state = self.attach(catalyst, (LABELED_AT + timedelta(hours=12)).isoformat())
        self.assertEqual((state['catalyst_family'], state['classification_source']), ('regulatory_positive', 'rules+model'))
        with closing(get_connection()) as conn:
            frozen = conn.execute('SELECT catalyst_family FROM candidate_catalysts WHERE candidate_id=?', ('cand-1',)).fetchone()
        self.assertEqual(frozen['catalyst_family'], 'regulatory_positive')

    def test_pending_selects_only_unlabelled_needs_review_with_ticker_newest_first(self):
        days = lambda count: (NOW - timedelta(days=count)).isoformat()
        titles = {'old': 'Nvidia stock slides after supplier chatter', 'new': RULE_FAILURE,
                  'labelled': 'Nvidia investors shrug at conference remarks', 'classified': 'Nvidia raises revenue guidance'}
        catalysts = {name: self.article(index, title, published_at=NOW - timedelta(days=3 * index + 3))[1]
                     for index, (name, title) in enumerate(titles.items())}
        self.record(catalysts['labelled'], LABEL_V1)
        with closing(get_connection()) as conn, conn:
            # Newer than every real catalyst, so only an exclusion rule keeps these out.
            for catalyst_id, ticker in (('catalyst:unknown:cand-9', 'NVDA'), ('catalyst:sector-discovery:tech', 'NVDA'),
                                        ('catalyst:no-ticker', None)):
                store_catalyst_state(build_catalyst_state(catalyst_id, [], ticker=ticker), conn=conn)
                conn.execute('UPDATE catalysts SET first_seen_time=? WHERE catalyst_id=?', (days(0), catalyst_id))
            for name, age in (('new', 1), ('old', 5), ('labelled', 3), ('classified', 2)):
                conn.execute('UPDATE catalysts SET first_seen_time=? WHERE catalyst_id=?', (days(age), catalysts[name]))
        self.assertEqual(self.pending(10, LABEL_V1), [catalysts['new'], catalysts['old']])
        self.assertEqual(self.pending(10, 'catalyst-llm-v2'), [catalysts['new'], catalysts['labelled'], catalysts['old']])
        self.assertEqual(self.pending(1, LABEL_V1), [catalysts['new']])

    def test_newer_uninformative_label_does_not_mask_an_older_usable_one(self):
        _, catalyst = self.article(1)
        self.record(catalyst, labeled_at=NOW - timedelta(hours=30), kind='regulatory', confidence='medium')
        self.record(catalyst, labeled_at=NOW - timedelta(hours=20), kind='legal', direction='negative', confidence='low')
        self.record(catalyst, labeled_at=NOW - timedelta(hours=10), kind='other', direction='unknown')
        self.assertEqual((self.latest(catalyst)['catalyst_type'], self.latest(catalyst)['confidence']), ('regulatory', 'medium'))
        self.refresh(catalyst)
        self.assertEqual(self.stored(catalyst)[0]['catalyst_family'], 'regulatory_positive')
        # With nothing usable, the newest label is still returned.
        _, other = self.article(2, 'Nvidia stock slides after supplier chatter')
        self.record(other, labeled_at=NOW - timedelta(hours=20), confidence='low')
        self.record(other, labeled_at=NOW - timedelta(hours=10), kind='other', direction='unknown')
        self.assertEqual(self.latest(other)['catalyst_type'], 'other')

    def join_later(self, number, catalyst, seen_at):
        article_id, joined = self.article(number, domain=f'site{number}.com')
        self.assertEqual(joined, catalyst)
        with closing(get_connection()) as conn, conn:
            conn.execute('UPDATE news_articles SET first_seen_time=? WHERE id=?', (seen_at.isoformat(), article_id))

    def test_unresolved_labelled_catalyst_is_re_asked_once_a_newer_article_joins(self):
        _, catalyst = self.article(1)
        self.record(catalyst, confidence='low')
        self.assertEqual(self.pending(10, LABEL_V1), [])
        self.join_later(2, catalyst, LABELED_AT - timedelta(hours=1))      # seen before the label: no new evidence
        self.assertEqual(self.pending(10, LABEL_V1), [])
        self.join_later(3, catalyst, LABELED_AT + timedelta(hours=1))
        self.assertEqual(self.pending(10, LABEL_V1), [catalyst])

    def test_re_asking_stops_after_three_labels_at_a_version(self):
        _, catalyst = self.article(1)
        for hours in (30, 20):
            self.record(catalyst, labeled_at=NOW - timedelta(hours=hours), kind='other', direction='unknown')
        self.join_later(2, catalyst, NOW - timedelta(hours=5))
        self.assertEqual(self.pending(10, LABEL_V1), [catalyst])
        self.record(catalyst, labeled_at=NOW - timedelta(hours=4), kind='other', direction='unknown')
        self.join_later(3, catalyst, NOW - timedelta(hours=1))
        self.assertEqual(self.pending(10, LABEL_V1), [])
        self.assertEqual(self.pending(10, 'catalyst-llm-v2'), [catalyst])

    def test_classified_catalyst_is_not_re_asked_when_articles_join(self):
        _, catalyst = self.article(1)
        self.record(catalyst)
        self.refresh(catalyst)
        self.join_later(2, catalyst, NOW - timedelta(hours=1))
        self.assertEqual(self.pending(10, LABEL_V1), [])

    def test_catalyst_without_articles_is_never_selected(self):
        with closing(get_connection()) as conn, conn:
            store_catalyst_state(build_catalyst_state('catalyst:empty', [], ticker='NVDA'), conn=conn)
            status = conn.execute("SELECT classification_status FROM catalysts WHERE catalyst_id='catalyst:empty'").fetchone()[0]
        self.assertEqual(status, 'needs_review')
        self.assertEqual(self.pending(10, LABEL_V1), [])

    def test_labelled_catalyst_registers_a_response_event(self):
        _, labelled = self.article(1)
        _, unlabelled = self.article(2, 'Nvidia stock slides after supplier chatter', published_at=NOW - timedelta(days=6))
        self.record(labelled, kind='regulatory', direction='positive')
        self.refresh(labelled)
        register_response_events(as_of=datetime.now(timezone.utc))
        with closing(get_connection()) as conn:
            events = {row['catalyst_id']: dict(row) for row in conn.execute('SELECT * FROM catalyst_response_events')}
        self.assertEqual(set(events), {labelled})
        self.assertEqual((events[labelled]['catalyst_family'], events[labelled]['direction']), ('regulatory_positive', 'positive'))
        self.assertEqual(json.loads(events[labelled]['state_json'])['classification_source'], 'rules+model')
        self.assertNotIn(unlabelled, events)


class CatalystLabelImmutabilityTests(unittest.TestCase):
    def setUp(self):
        temporary_database(self)
        self.conn = get_connection()
        self.addCleanup(self.conn.close)
        self.insert('catalyst:one', 'v1')

    def insert(self, catalyst_id, version, labeled_at=LABELED_AT.isoformat()):
        with self.conn:
            self.conn.execute('''INSERT INTO catalyst_model_labels (catalyst_id, label_version, catalyst_type, direction,
                confidence, anchor_article_id, model, answered_by_json, labeled_at) VALUES (?, ?, 'legal', 'negative',
                'high', 1, 'test-model', '{}', ?)''', (catalyst_id, version, labeled_at))

    def test_update_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError), self.conn:
            self.conn.execute("UPDATE catalyst_model_labels SET direction='positive'")
        self.assertEqual(self.conn.execute('SELECT direction FROM catalyst_model_labels').fetchone()[0], 'negative')

    def test_delete_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError), self.conn:
            self.conn.execute('DELETE FROM catalyst_model_labels')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM catalyst_model_labels').fetchone()[0], 1)

    def test_a_new_label_version_for_the_same_catalyst_is_allowed(self):
        self.insert('catalyst:one', 'v2')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM catalyst_model_labels').fetchone()[0], 2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert('catalyst:one', 'v2')

    def test_a_later_label_at_the_same_version_is_allowed(self):
        self.insert('catalyst:one', 'v1', NOW.isoformat())
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM catalyst_model_labels').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
