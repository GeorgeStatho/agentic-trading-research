"""Plan A slice A2: the classifier stage, runner, settings, collector hook, backfill CLI and report.

Only the model call is mocked (`ask_model` of the stage). Everything else runs against a real temporary database.
New modules are imported on use so each test fails on its own while they do not exist yet.
"""
from __future__ import annotations

from contextlib import ExitStack, closing, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from catalyst_models import CATALYST_TYPES, CLASSIFICATION_VERSION, LABEL_VERSION, classify_catalyst
from db_common import get_connection
from market_db_modules.pricing_research import pricing_report, register_response_events
from news_db_modules import catalyst_labels
from news_db_modules.catalysts import build_catalyst_state, refresh_catalyst
from news_db_modules.entity_articles import add_company_news_article

from tests.db_fixtures import temporary_database

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc)
RULE_FAILURE = 'Nvidia shares jump as investors cheer AI demand'    # no rule matches: other / unknown
RULE_HIT = 'Nvidia raises revenue guidance after strong demand'     # rules classify it
QUOTA_TEXT = 'Model returned empty content with finish reason MAX_TOKENS'


def stage():
    return importlib.import_module('agent_stages.catalyst_classifier')


def runner():
    return importlib.import_module('services.discovery.catalyst_classification')


def settings_module():
    return importlib.import_module('services.core.settings')


def model_answer(text, model):
    from agent_helpers.llm_client import ModelAnswer
    return ModelAnswer(text=text, provider='test', model=model, model_version='v9', fallback=True)


def good(catalyst_id, kind='regulatory', direction='positive', confidence='high'):
    return {'catalyst_id': catalyst_id, 'catalyst_type': kind, 'direction': direction, 'confidence': confidence}


class StubModel:
    """Stands in for the stage's `ask_model`. `respond(batch_items)` returns the labels list, raw text or an exception."""

    def __init__(self, respond=None):
        self.respond = respond or (lambda items: [good(item['catalyst_id']) for item in items])
        self.calls = []

    def __call__(self, *args, **kwargs):
        call = dict(zip(('client', 'model', 'system_prompt', 'user_prompt'), args), **kwargs)
        self.calls.append(call)
        outcome = self.respond(json.loads(call['user_prompt'])['catalysts'])
        if isinstance(outcome, Exception):
            raise outcome
        return model_answer(outcome if isinstance(outcome, str) else json.dumps({'labels': outcome}), call['model'])

    def asked(self):
        return [item['catalyst_id'] for call in self.calls for item in json.loads(call['user_prompt'])['catalysts']]


class ClassifierCase(unittest.TestCase):
    def setUp(self):
        self.db = temporary_database(self)
        self.count = 0
        with closing(get_connection()) as conn, conn:
            conn.execute("INSERT INTO sectors(id, sector_key, name) VALUES (1, 'technology', 'Technology')")
            conn.execute("INSERT INTO industries(id, sector_id, industry_key, name) VALUES (1, 1, 'chips', 'Chips')")
            conn.execute("INSERT INTO companies(id, industry_id, symbol, name) VALUES (1, 1, 'NVDA', 'Nvidia')")

    def article(self, title=None, *, domain='reuters.com', published=None, body='Reuters reports the move.',
                summary=None, first_seen=None):
        """Add one article. Catalysts created in call order are newest-first: later ones are older."""
        self.count += 1
        number = self.count
        article_id = add_company_news_article(company_id=1, source=domain, article_key=str(number),
            title=title or f'Nvidia shares drift amid chatter {number}', body=body, summary=summary,
            source_url=f'https://{domain}/story/{number}',
            published_at=(published or NOW - timedelta(days=2 * number + 2)).isoformat(),
            recency_score=1, source_reputation_score=.95, directness_score=.95, factuality_score=.95)
        with closing(get_connection()) as conn, conn:
            conn.execute('UPDATE news_articles SET first_seen_time=? WHERE id=?',
                         ((first_seen or NOW - timedelta(hours=number)).isoformat(), article_id))
            catalyst = conn.execute('SELECT catalyst_id FROM news_articles WHERE id=?', (article_id,)).fetchone()[0]
            refresh_catalyst(catalyst, conn=conn)
        return catalyst, article_id

    def catalysts(self, count):
        return [self.article()[0] for _ in range(count)]

    def stored(self, catalyst_id):
        with closing(get_connection()) as conn:
            row = conn.execute('SELECT * FROM catalysts WHERE catalyst_id=?', (catalyst_id,)).fetchone()
        return dict(row), json.loads(row['state_json'])

    def label_rows(self):
        with closing(get_connection()) as conn:
            return {row['catalyst_id']: dict(row) for row in conn.execute('SELECT * FROM catalyst_model_labels')}

    def apply_label(self, catalyst_id, kind='regulatory', direction='positive', confidence='high'):
        with closing(get_connection()) as conn, conn:
            catalyst_labels.record_catalyst_label(catalyst_id, label_version=LABEL_VERSION, catalyst_type=kind,
                direction=direction, confidence=confidence, anchor_article_id=1, model='test-model',
                answered_by={'model': 'test-model'}, conn=conn)
            refresh_catalyst(catalyst_id, conn=conn)

    def classify(self, model=None, **kwargs):
        model = model or StubModel()
        with patch.object(stage(), 'ask_model', model):
            return runner().classify_pending_catalysts(**kwargs), model


class SettingsTests(unittest.TestCase):
    def settings(self, **environ):
        return settings_module().Settings.from_environ(environ)

    def test_classifier_is_off_with_a_cap_of_200_by_default(self):
        found = self.settings().catalyst_classifier
        self.assertEqual((found.enabled, found.max_per_cycle), (False, 200))

    def test_environment_turns_it_on_and_sets_the_cap(self):
        found = self.settings(CATALYST_CLASSIFIER_ENABLED='true', CATALYST_CLASSIFIER_MAX_PER_CYCLE='50').catalyst_classifier
        self.assertEqual((found.enabled, found.max_per_cycle), (True, 50))
        self.assertFalse(self.settings(CATALYST_CLASSIFIER_ENABLED='false').catalyst_classifier.enabled)

    def test_model_inherits_the_company_opportunist_unless_overridden(self):
        self.assertEqual(self.settings(COMPANY_OPPURUNTIST_MODEL='opp').models.catalyst_classifier.model, 'opp')
        both = self.settings(COMPANY_OPPURUNTIST_MODEL='opp', CATALYST_CLASSIFIER_MODEL='cheap')
        self.assertEqual(both.models.catalyst_classifier.model, 'cheap')
        self.assertEqual(both.models.company_opportunist.model, 'opp')


class StageTests(unittest.TestCase):
    def test_model_wrapper_asks_for_the_label_schema_at_temperature_zero(self):
        answer = model_answer('{}', 'm')
        with patch.object(stage(), 'ask_classifier_model', return_value=answer) as asked:
            self.assertIs(stage().ask_model('client', 'm', 'system', 'user'), answer)
        self.assertEqual(asked.call_args.args[:4], ('client', 'm', 'system', 'user'))
        self.assertEqual(asked.call_args.kwargs['temperature'], 0)
        self.assertIs(asked.call_args.kwargs['response_schema'], stage().CATALYST_LABELS_SCHEMA)

    def test_schema_pins_the_enums(self):
        schema = stage().CATALYST_LABELS_SCHEMA
        item = schema['properties']['labels']['items']
        self.assertEqual(schema['required'], ['labels'])
        self.assertEqual(sorted(item['required']), ['catalyst_id', 'catalyst_type', 'confidence', 'direction'])
        self.assertEqual(item['properties']['catalyst_type']['enum'], list(CATALYST_TYPES))
        self.assertEqual(item['properties']['direction']['enum'], ['positive', 'negative', 'mixed', 'unknown'])
        self.assertEqual(item['properties']['confidence']['enum'], ['high', 'medium', 'low'])


class RunnerSelectionTests(ClassifierCase):
    def test_labels_apply_and_are_stored_with_provenance(self):
        (first, article_id), (second, _) = self.article(RULE_FAILURE), self.article()
        result, model = self.classify(StubModel(lambda items: [good(first), good(second, 'legal', 'negative', 'medium')]))
        self.assertEqual(result, {'selected': 2, 'labeled': 2, 'applied': 2, 'low_confidence': 0, 'invalid': 0,
                                  'quota_stopped': False, 'unavailable_stopped': False})
        rows = self.label_rows()
        sent_model = model.calls[0]['model']
        self.assertEqual((rows[first]['label_version'], rows[first]['catalyst_type'], rows[first]['direction'],
                          rows[first]['confidence'], rows[first]['anchor_article_id'], rows[first]['model']),
                         (LABEL_VERSION, 'regulatory', 'positive', 'high', article_id, sent_model))
        self.assertEqual(json.loads(rows[first]['answered_by_json']),
                         {'provider': 'test', 'model': sent_model, 'model_version': 'v9', 'fallback': True})
        row, state = self.stored(first)
        self.assertEqual((row['classification_status'], row['catalyst_family'], state['classification_source']),
                         ('classified', 'regulatory_positive', 'rules+model'))
        self.assertEqual(self.stored(second)[0]['catalyst_family'], 'legal_negative')

    def test_newest_first_in_batches_of_twenty(self):
        ordered = self.catalysts(45)
        result, model = self.classify()
        self.assertEqual([len(json.loads(call['user_prompt'])['catalysts']) for call in model.calls], [20, 20, 5])
        self.assertEqual(model.asked(), ordered)
        self.assertEqual((result['selected'], result['labeled']), (45, 45))

    def test_limit_caps_the_selection_and_none_uses_max_per_cycle(self):
        ordered = self.catalysts(5)
        result, model = self.classify(limit=3)
        self.assertEqual((result['selected'], model.asked()), (3, ordered[:3]))
        settings = settings_module()
        capped = replace(settings.get_settings().catalyst_classifier, max_per_cycle=1)
        with settings.use_settings(catalyst_classifier=capped):
            result, model = self.classify(limit=None)
        self.assertEqual((result['selected'], model.asked()), (1, ordered[3:4]))

    def test_runs_with_the_classifier_setting_off_so_the_backfill_can_use_it(self):
        self.assertFalse(settings_module().get_settings().catalyst_classifier.enabled)
        self.catalysts(1)
        result, model = self.classify()
        self.assertEqual((result['labeled'], len(model.calls)), (1, 1))

    def test_placeholders_unticked_classified_and_labelled_catalysts_are_never_asked(self):
        wanted, _ = self.article()
        classified, _ = self.article(RULE_HIT)
        labelled, _ = self.article()
        self.apply_label(labelled, confidence='low')     # recorded at the current version, still needs review
        from news_db_modules.catalysts import build_catalyst_state, store_catalyst_state
        with closing(get_connection()) as conn, conn:
            for catalyst_id, ticker in (('catalyst:unknown:cand-9', 'NVDA'), ('catalyst:sector-discovery:tech', 'NVDA'),
                                        ('catalyst:no-ticker', None)):
                store_catalyst_state(build_catalyst_state(catalyst_id, [], ticker=ticker), conn=conn)
                conn.execute('UPDATE catalysts SET first_seen_time=? WHERE catalyst_id=?', (NOW.isoformat(), catalyst_id))
        result, model = self.classify()
        self.assertEqual((model.asked(), result['selected']), ([wanted], 1))
        self.assertNotIn(classified, model.asked())

    def test_a_second_run_asks_nothing(self):
        self.catalysts(3)
        self.classify()
        result, model = self.classify()
        self.assertEqual((result['selected'], result['labeled'], model.calls), (0, 0, []))


class RunnerInputTests(ClassifierCase):
    def test_prompt_carries_the_anchor_article_and_the_label_vocabulary(self):
        title = 'Nvidia shares drift amid chatter about supplier'
        _, later = self.article(title, published=NOW - timedelta(days=2), body='later body', summary='later summary')
        catalyst, earlier = self.article(title, domain='newswire.example.com', published=NOW - timedelta(days=2, hours=6),
                                         body='abcdefghij' * 300, summary='earlier summary')
        self.assertEqual(self.stored(catalyst)[1]['article_count'], 2)
        _, model = self.classify()
        system, payload = model.calls[0]['system_prompt'], json.loads(model.calls[0]['user_prompt'])
        self.assertEqual(payload['catalysts'], [{'catalyst_id': catalyst, 'ticker': 'NVDA', 'title': title,
                         'summary': 'earlier summary', 'body': ('abcdefghij' * 300)[:1500], 'later_headlines': [title]}])
        self.assertEqual(self.label_rows()[catalyst]['anchor_article_id'], earlier)
        for word in (*CATALYST_TYPES, 'positive', 'negative', 'mixed', 'unknown', 'high', 'medium', 'low'):
            self.assertIn(word, system)
        self.assertIn('untrusted', system.lower())


    def test_a_re_ask_shows_the_newer_headlines(self):
        catalyst, _ = self.article(RULE_FAILURE)
        self.classify(StubModel(lambda items: [good(catalyst, confidence='low')]))
        follow_up = 'Nvidia shares extend gains as the AI demand story spreads'
        _, joined = self.article(RULE_FAILURE, domain='newswire.example.com', published=NOW, first_seen=NOW + timedelta(hours=1))
        with closing(get_connection()) as conn, conn:
            conn.execute('UPDATE news_articles SET title=? WHERE id=?', (follow_up, joined))
        result, model = self.classify()
        self.assertEqual((result['selected'], result['applied']), (1, 1))
        sent, = json.loads(model.calls[0]['user_prompt'])['catalysts']
        self.assertEqual((sent['title'], sent['later_headlines']), (RULE_FAILURE, [follow_up]))
        self.assertEqual(len(self.label_rows()), 1)   # keyed by catalyst; two rows exist
        with closing(get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM catalyst_model_labels').fetchone()[0], 2)


class RunnerValidationTests(ClassifierCase):
    def test_bad_items_are_dropped_and_counted(self):
        a, b, d, e, f, g, h, spare = self.catalysts(8)

        def answer(items):
            return [good(a), good(b, 'legal', 'negative', 'low'), good('catalyst:foreign'),
                    good(d, 'not-a-type'), good(a, 'legal', 'negative'),         # duplicate: the first one wins
                    good(e, 'other', 'unknown'), good(f, direction='sideways'), good(g, confidence='certain'),
                    'garbage', {'catalyst_id': h, 'catalyst_type': 'legal', 'direction': 'negative'}]

        result, _ = self.classify(StubModel(answer))
        self.assertEqual(result, {'selected': 8, 'labeled': 3, 'applied': 1, 'low_confidence': 1, 'invalid': 7,
                                  'quota_stopped': False, 'unavailable_stopped': False})
        rows = self.label_rows()
        self.assertEqual(set(rows), {a, b, e})
        self.assertEqual((rows[a]['catalyst_type'], rows[a]['direction']), ('regulatory', 'positive'))

    def test_no_information_label_is_recorded_changes_nothing_and_is_not_asked_again(self):
        catalyst, = self.catalysts(1)
        result, _ = self.classify(StubModel(lambda items: [good(catalyst, 'other', 'unknown')]))
        self.assertEqual(result, {'selected': 1, 'labeled': 1, 'applied': 0, 'low_confidence': 0, 'invalid': 0,
                                  'quota_stopped': False, 'unavailable_stopped': False})
        self.assertEqual(set(self.label_rows()), {catalyst})
        row, state = self.stored(catalyst)
        self.assertEqual((row['classification_status'], state['classification_source'], row['classification_version']),
                         ('needs_review', 'rules', CLASSIFICATION_VERSION))
        again, model = self.classify()
        self.assertEqual((again['selected'], model.calls), (0, []))

    def test_other_type_with_a_real_direction_is_kept(self):
        catalyst, = self.catalysts(1)
        result, _ = self.classify(StubModel(lambda items: [good(catalyst, 'other', 'negative')]))
        self.assertEqual((result['labeled'], result['invalid']), (1, 0))
        self.assertEqual(self.label_rows()[catalyst]['direction'], 'negative')

    def test_low_confidence_label_is_stored_but_not_applied(self):
        catalyst, = self.catalysts(1)
        result, _ = self.classify(StubModel(lambda items: [good(catalyst, confidence='low')]))
        self.assertEqual((result['labeled'], result['applied'], result['low_confidence']), (1, 0, 1))
        row, state = self.stored(catalyst)
        self.assertEqual((self.label_rows()[catalyst]['confidence'], row['classification_status'], state['classification_source']),
                         ('low', 'needs_review', 'rules'))

    def test_unparseable_answer_labels_nothing_without_raising(self):
        self.catalysts(2)
        result, _ = self.classify(StubModel(lambda items: 'I cannot help with that'))
        self.assertEqual((result['selected'], result['labeled'], result['quota_stopped']), (2, 0, False))
        self.assertEqual(self.label_rows(), {})


class RunnerFailureTests(ClassifierCase):
    def test_quota_exhaustion_stops_the_loop_without_raising(self):
        from agent_helpers.llm_client import ModelQuotaExhausted
        self.catalysts(45)
        outcomes = iter([None, ModelQuotaExhausted('daily quota used up')])

        def respond(items):
            failure = next(outcomes)
            return failure or [good(item['catalyst_id']) for item in items]

        result, model = self.classify(StubModel(respond))
        self.assertEqual(len(model.calls), 2)            # the third batch is never asked
        self.assertEqual((result['labeled'], result['quota_stopped']), (20, True))
        self.assertEqual(len(self.label_rows()), 20)

    def test_quota_on_the_first_call_is_reported_not_raised(self):
        from agent_helpers.llm_client import ModelQuotaExhausted
        self.catalysts(2)
        result, _ = self.classify(StubModel(lambda items: ModelQuotaExhausted('no quota')))
        self.assertEqual((result['selected'], result['labeled'], result['quota_stopped']), (2, 0, True))

    def test_max_tokens_empty_answer_skips_that_batch_and_carries_on(self):
        self.catalysts(25)
        outcomes = iter([RuntimeError(QUOTA_TEXT), None])

        def respond(items):
            failure = next(outcomes)
            return failure or [good(item['catalyst_id']) for item in items]

        result, model = self.classify(StubModel(respond))
        self.assertEqual((len(model.calls), result['labeled'], result['quota_stopped']), (2, 5, False))

    def test_other_model_errors_propagate_but_finished_batches_are_kept(self):
        self.catalysts(25)
        outcomes = iter([None, RuntimeError('model server fell over')])

        def respond(items):
            failure = next(outcomes)
            return failure or [good(item['catalyst_id']) for item in items]

        with self.assertRaisesRegex(RuntimeError, 'fell over'):
            self.classify(StubModel(respond))
        self.assertEqual(len(self.label_rows()), 20)

    def test_an_unavailable_batch_is_skipped_and_the_run_carries_on(self):
        from agent_helpers.llm_client import ModelUnavailable
        self.catalysts(45)
        outcomes = iter([ModelUnavailable('500 INTERNAL'), None, None])

        def respond(items):
            failure = next(outcomes)
            return failure or [good(item['catalyst_id']) for item in items]

        result, model = self.classify(StubModel(respond))
        self.assertEqual(len(model.calls), 3)
        self.assertEqual((result['labeled'], result['quota_stopped'], result['unavailable_stopped']), (25, False, False))

    def test_three_unavailable_batches_in_a_row_stop_the_run_without_raising(self):
        from agent_helpers.llm_client import ModelUnavailable
        self.catalysts(100)
        result, model = self.classify(StubModel(lambda items: ModelUnavailable('500 INTERNAL')))
        self.assertEqual(len(model.calls), 3)            # batches four and five are never asked
        self.assertEqual((result['labeled'], result['unavailable_stopped']), (0, True))

    def test_a_good_batch_resets_the_unavailable_count(self):
        from agent_helpers.llm_client import ModelUnavailable
        self.catalysts(120)
        down = ModelUnavailable('500 INTERNAL')
        outcomes = iter([down, down, None, down, down, None])

        def respond(items):
            failure = next(outcomes)
            return failure or [good(item['catalyst_id']) for item in items]

        result, model = self.classify(StubModel(respond))
        self.assertEqual(len(model.calls), 6)
        self.assertEqual((result['labeled'], result['unavailable_stopped']), (40, False))

    def test_labels_and_refresh_commit_together(self):
        catalyst, = self.catalysts(1)
        with patch.object(runner(), 'refresh_catalyst', side_effect=RuntimeError('refresh failed')):
            with self.assertRaisesRegex(RuntimeError, 'refresh failed'):
                self.classify()
        self.assertEqual(self.label_rows(), {})
        self.assertEqual(self.stored(catalyst)[0]['classification_status'], 'needs_review')


class RunnerRegistrationTests(ClassifierCase):
    def test_a_labelled_catalyst_registers_a_response_event_in_the_same_flow(self):
        labelled, _ = self.article(RULE_FAILURE, published=NOW - timedelta(days=2))
        unlabelled, _ = self.article('Nvidia stock slides after supplier chatter', published=NOW - timedelta(days=6))
        self.classify(StubModel(lambda items: [good(labelled)]))
        register_response_events(as_of=datetime.now(timezone.utc))
        with closing(get_connection()) as conn:
            events = {row['catalyst_id']: dict(row) for row in conn.execute('SELECT * FROM catalyst_response_events')}
        self.assertEqual(set(events), {labelled})
        self.assertEqual(json.loads(events[labelled]['state_json'])['classification_source'], 'rules+model')
        self.assertNotIn(unlabelled, events)


class NoInformationMergeTests(unittest.TestCase):
    def test_a_label_that_changes_nothing_leaves_the_state_rules_only(self):
        when = (NOW - timedelta(days=2)).isoformat()
        article = {'id': 1, 'title': RULE_FAILURE, 'summary': '', 'event_time': when, 'first_seen_time': when,
                   'primary_ticker': 'NVDA'}
        as_of = NOW.isoformat()
        unlabelled = build_catalyst_state('catalyst:merge', [article], ticker='NVDA', as_of=as_of)
        labelled = build_catalyst_state('catalyst:merge', [article], ticker='NVDA', as_of=as_of,
                                        label={'catalyst_type': 'other', 'direction': 'unknown', 'confidence': 'high'})
        self.assertEqual(labelled, unlabelled)
        self.assertEqual(labelled['classification_source'], 'rules')


class CollectorHookTests(ClassifierCase):
    def cycle(self, classifier_enabled, model, on_responses=None):
        collector = importlib.import_module('news_collector_main')
        settings = settings_module()
        config = replace(settings.get_settings().catalyst_classifier, enabled=classifier_enabled, max_per_cycle=2)
        seen = []

        def collect_responses(*args, **kwargs):
            seen.append(on_responses() if on_responses else None)
            return {}

        with ExitStack() as stack:
            stack.enter_context(settings.use_settings(catalyst_classifier=config))
            stack.enter_context(patch.object(stage(), 'ask_model', model))
            stack.enter_context(patch.object(collector, 'run_news_collection_pipeline', return_value={}))
            stack.enter_context(patch.object(collector.JsonFileWriter, 'write'))
            for target in ('services.gates.market_regime.build_market_regime_snapshot',
                           'services.evaluation.forward_evaluation.run_forward_evaluation_cycle',
                           'services.discovery.company_evidence_service.collect_company_evidence',
                           'services.discovery.event_discovery.discover_company_events'):
                stack.enter_context(patch(target, return_value={}))
            stack.enter_context(patch('services.discovery.catalyst_response_collector.collect_catalyst_responses',
                                      side_effect=collect_responses))
            result = collector.run_news_collection_cycle()
        return result, seen

    def test_enabled_classifier_runs_before_response_collection_and_is_capped(self):
        ordered = self.catalysts(3)
        model = StubModel()
        result, seen = self.cycle(True, model, on_responses=lambda: self.stored(ordered[0])[0]['classification_status'])
        self.assertEqual(seen, ['classified'])                      # labels were in place when responses were collected
        self.assertEqual((result['catalyst_classification']['selected'], result['catalyst_classification']['labeled']), (2, 2))
        self.assertEqual(model.asked(), ordered[:2])

    def test_disabled_classifier_makes_no_model_calls(self):
        self.catalysts(2)
        model = StubModel()
        _, seen = self.cycle(False, model)
        self.assertEqual((model.calls, self.label_rows(), len(seen)), ([], {}, 1))

    def test_a_classifier_failure_does_not_stop_the_cycle(self):
        self.catalysts(1)
        with self.assertLogs(level='ERROR'):
            result, seen = self.cycle(True, StubModel(lambda items: RuntimeError('model server fell over')))
        self.assertEqual(len(seen), 1)
        self.assertIsNone(result['catalyst_classification'])


class ReportTests(ClassifierCase):
    def seed(self):
        rules, _ = self.article(RULE_HIT)
        model_labelled, _ = self.article()
        low, _ = self.article()
        unmapped, _ = self.article()
        self.apply_label(model_labelled, 'regulatory', 'positive', 'high')
        self.apply_label(low, 'legal', 'negative', 'low')
        self.apply_label(unmapped, 'supply_chain', 'negative', 'high')
        return rules, model_labelled, low, unmapped

    def test_pricing_report_counts_by_classification_source_and_unmapped_types(self):
        self.seed()
        report = pricing_report()
        self.assertEqual(report['classification_sources'], {'rules': 2, 'rules+model': 2})
        self.assertEqual(report['needs_review_without_family'], {'supply_chain': 1})

    def test_pricing_report_without_labels_is_all_rules(self):
        self.article(RULE_HIT)
        report = pricing_report()
        self.assertEqual((report['classification_sources'], report['needs_review_without_family']), ({'rules': 1}, {}))


class BackfillCliTests(ClassifierCase):
    def run_cli(self, *argv, model=None):
        spec = importlib.util.spec_from_file_location('catalysts_cli', ROOT / 'scripts' / 'catalysts.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        out = io.StringIO()
        with patch.object(sys, 'argv', ['catalysts.py', '--db', str(self.db), *argv]), \
                patch.object(stage(), 'ask_model', model or StubModel()), redirect_stdout(out):
            cli.main()
        return json.loads(out.getvalue())

    def test_classify_labels_up_to_the_limit_and_prints_the_summary(self):
        self.catalysts(3)
        result = self.run_cli('classify', '--limit', '2')
        self.assertEqual((result['selected'], result['labeled'], result['quota_stopped']), (2, 2, False))
        self.assertEqual(len(self.label_rows()), 2)

    def test_classify_rejects_a_non_positive_limit(self):
        with self.assertRaises(SystemExit):
            self.run_cli('classify', '--limit', '0')

    def test_status_counts_by_status_source_type_and_confidence(self):
        ReportTests.seed(self)
        result = self.run_cli('status')
        self.assertEqual(result['by_status'], {'classified': 2, 'needs_review': 2})
        self.assertEqual(result['by_classification_source'], {'rules': 2, 'rules+model': 2})
        self.assertEqual(result['by_type'], {classify_catalyst(RULE_HIT)['catalyst_type']: 1, 'regulatory': 1,
                                             'supply_chain': 1, 'other': 1})
        self.assertEqual(result['by_confidence'], {'high': 2, 'low': 1})

    def test_review_samples_labelled_catalysts_with_their_headlines(self):
        first, _ = self.article(RULE_FAILURE)
        second, _ = self.article()
        self.article()                                   # never labelled: not in the sample
        self.apply_label(first, 'regulatory', 'positive', 'high')
        self.apply_label(second, 'legal', 'negative', 'medium')
        sample = {item['catalyst_id']: item for item in self.run_cli('review', '--sample', '20')['sample']}
        self.assertEqual(set(sample), {first, second})
        self.assertEqual({key: sample[first][key] for key in ('title', 'catalyst_type', 'direction', 'confidence')},
                         {'title': RULE_FAILURE, 'catalyst_type': 'regulatory', 'direction': 'positive',
                          'confidence': 'high'})
        self.assertEqual(len(self.run_cli('review', '--sample', '1')['sample']), 1)


class WiringTests(unittest.TestCase):
    def test_pipeline_version_hashes_the_label_store_and_the_new_stage_files(self):
        from services.core.pipeline_version import pipeline_source_hashes
        hashed = set(pipeline_source_hashes())
        for path in ('Data/news_db_modules/catalyst_labels.py',
                     'Python Scripts/agentCallers/agent_stages/catalyst_classifier.py',
                     'Python Scripts/agentCallers/agent_helpers/catalyst_classifier.py',
                     'Python Scripts/services/discovery/catalyst_classification.py'):
            self.assertIn(path, hashed)

    def test_env_example_and_docs_describe_the_classifier(self):
        env = (ROOT / '.env.example').read_text(encoding='utf-8')
        self.assertIn('CATALYST_CLASSIFIER_ENABLED=false', env)
        self.assertIn('CATALYST_CLASSIFIER_MAX_PER_CYCLE=200', env)
        self.assertIn('CATALYST_CLASSIFIER_ENABLED', (ROOT / 'docs' / 'RESEARCH_GUIDE.md').read_text(encoding='utf-8'))
        code_map = (ROOT / 'docs' / 'CODE_MAP.md').read_text(encoding='utf-8')
        for module in ('catalyst_classifier.py', 'catalyst_classification.py', 'catalyst_labels.py'):
            self.assertIn(module, code_map)


if __name__ == '__main__':
    unittest.main()
