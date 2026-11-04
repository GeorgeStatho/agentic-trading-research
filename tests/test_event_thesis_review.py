"""Phase 6: material events trigger shadow thesis reviews of open positions."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests.research_foundation_fixtures import database, CATALYST
from db_common import get_connection
from market_db_modules.discovery_queue import claim_discovery_batch, enqueue_discovery_event
from market_db_modules.thesis_event_reviews import thesis_event_reviews
from news_db_modules.bootstrap import initialize_database as initialize_news
from research_foundation_contracts import DiscoveryQueueItem
from services.discovery.discovery_policy import DiscoveryPolicy
from services.thesis.thesis_event_review_policy import EventThesisReviewPolicy
from services.options.position_manager import OptionPositionManagerService
from services.thesis.thesis_event_review import EventThesisReviewService
from services.thesis.thesis_validator_service import ThesisValidatorService, ThesisValidatorSettings


OPENED = datetime(2026, 9, 21, 14, tzinfo=timezone.utc)
EVENT_AT = datetime(2026, 9, 22, 13, 30, tzinfo=timezone.utc)  # 09:30 New York
OPTION = 'NVDA261016C00150000'
SETTLED = timedelta(minutes=16)


def iso(value):
    return value.isoformat()


def position(**overrides):
    return {'symbol': OPTION, 'decision': 'hold', 'quantity': 2, 'mid_price': 3.1, 'pending_order_id': '', **overrides}


def article(article_id, *, seen=EVENT_AT, title='NVDA cuts revenue guidance'):
    return {'id': article_id, 'title': title, 'body': f'{title}. Management lowered the outlook.', 'source': 'wire',
            'event_time': iso(seen), 'first_seen_time': iso(seen), 'root_event_id': f'root:{article_id}'}


def verdict(state='invalidated', would_open='no', evidence_ids=(101,)):
    return {'thesis_state': state, 'would_open_today': would_open, 'evidence_ids': list(evidence_ids),
            'rationale': 'Guidance cut contradicts the entry catalyst.', 'confidence': .8, 'consistency': 'unanimous',
            'grounded': True, 'flip_detected': False, 'sample_count': 3, 'raw_samples': [],
            'prompt_version': 'thesis-adjudicator-v1', 'model': 'fixture'}


def base_pack(thesis, position_summary, *, news_limit):
    return {'version': 'thesis-evidence-pack-v1', 'thesis': {'symbol': thesis['symbol']},
            'news_since_entry': [{'article_id': 7, 'title': 'Older coverage'}], 'allowed_evidence_article_ids': [7]}


class EventReviewFixture(unittest.TestCase):
    def setUp(self):
        self.db = database(self)
        initialize_news(db_path=self.db)
        self.adjudicate = Mock(return_value=verdict())
        with closing(get_connection(self.db)) as conn, conn:
            self.catalyst = json.loads(conn.execute('SELECT state_json FROM catalysts WHERE catalyst_id=?',
                                                    (CATALYST,)).fetchone()[0])
            self.thesis_id = self.add_thesis(conn, OPTION)

    def add_thesis(self, conn, option_symbol, candidate_id='candidate-1'):
        return conn.execute('''INSERT INTO position_theses (symbol, option_symbol, opened_at, company_id, candidate_id)
            VALUES ('NVDA', ?, ?, 1, ?)''', (option_symbol, iso(OPENED), candidate_id)).lastrowid

    def enqueue(self, revision='revision-1', *, at=EVENT_AT, articles=None, signature=None):
        event = {'company': {'id': 1, 'symbol': 'NVDA', 'name': 'Fixture'}, 'catalyst_id': CATALYST,
                 'evidence_revision': revision, 'package_id': None, 'catalyst': self.catalyst,
                 'articles': [article(101)] if articles is None else articles, 'published_at': iso(at),
                 'observed_at': iso(at), 'change_kind': 'material',
                 'signature': signature or {'roots': [{'root': 'root:101', 'content': revision}]}}
        item = DiscoveryQueueItem(company_id=1, catalyst_id=CATALYST, evidence_revision=revision, purpose='position_review',
                                  origin='position_review', created_at=iso(at), priority_components={'review_urgency': 20})
        return enqueue_discovery_event(item, event, expires_at=iso(at + timedelta(days=7)), db_path=self.db)

    def reviewer(self, at, *, mode='shadow', validator=None, **policy):
        return EventThesisReviewService(
            policy=EventThesisReviewPolicy(mode=mode, **policy),
            validator_settings=validator or ThesisValidatorSettings(mode='live', max_daily_llm_exits=2),
            discovery_policy=DiscoveryPolicy(mode='shadow'), adjudicate=self.adjudicate, build_pack=base_pack,
            clock=lambda: at, logger=logging.getLogger('event-review-test'))

    def review(self, at=EVENT_AT + SETTLED, positions=None, **kwargs):
        return self.reviewer(at, **kwargs).run(positions=[position()] if positions is None else positions, db_path=self.db)

    def records(self):
        return thesis_event_reviews(self.thesis_id, db_path=self.db)

    def statuses(self):
        return [record['status'] for record in self.records()]


class AcceptanceTests(EventReviewFixture):
    def test_morning_contradiction_is_reviewed_before_the_daily_schedule(self):
        self.enqueue()
        morning = EVENT_AT + SETTLED
        daily = ThesisValidatorService(settings=ThesisValidatorSettings()).run_due_validation(positions=[position()], now=morning)
        self.review(morning)
        self.assertEqual((daily, self.statuses(), self.records()[0]['thesis_state']), (None, ['evaluated'], 'invalidated'))

    def test_duplicate_evidence_is_not_reviewed_again(self):
        signature = {'roots': [{'root': 'root:101', 'content': 'same text'}]}
        self.enqueue('revision-1', signature=signature)
        self.review()
        self.enqueue('revision-2', at=EVENT_AT + timedelta(hours=1), signature=signature)
        self.review(EVENT_AT + timedelta(hours=1) + SETTLED)
        self.assertEqual((self.adjudicate.call_count, self.statuses()), (1, ['evaluated', 'coalesced']))

    def test_review_failure_does_not_reach_mechanical_management(self):
        manager = OptionPositionManagerService(settings=SimpleNamespace(), paths=SimpleNamespace(), trading_gateway=Mock(),
                                               event_thesis_reviewer=Mock(run=Mock(side_effect=RuntimeError('model down'))),
                                               logger=logging.getLogger('event-review-test'))
        with self.assertLogs('event-review-test', 'ERROR'):
            self.assertEqual(manager._run_event_thesis_reviews(positions=[position()]), {'error': 'model down'})


class ShadowAuthorityTests(EventReviewFixture):
    def test_live_validator_mode_does_not_extend_to_event_reviews(self):
        self.enqueue()
        self.review()
        record = self.records()[0]
        self.assertEqual((record['would_be_action'], record['executed'], record['authority']), ('close', False, 'shadow_only'))

    def test_rules_exit_takes_precedence_without_a_model_call(self):
        self.enqueue()
        self.review(positions=[position(decision='sell')])
        self.assertEqual((self.statuses(), self.adjudicate.call_count), (['rules_exit_precedence'], 0))


class DebounceAndBudgetTests(EventReviewFixture):
    def test_daily_limit_defers_the_review_to_the_next_market_day(self):
        for index in range(4):
            at = EVENT_AT + timedelta(hours=index)
            self.enqueue(f'revision-{index}', at=at)
            self.review(at + SETTLED)
        self.assertEqual((self.adjudicate.call_count, self.statuses()), (3, ['evaluated'] * 3))
        with closing(get_connection(self.db)) as conn:
            item = conn.execute("SELECT status, completion_reason, next_attempt_at FROM discovery_queue "
                                "WHERE status != 'completed'").fetchone()
        # Midnight New York (EDT) on 2026-09-23, when the per-position limit resets.
        self.assertEqual(tuple(item), ('deferred', 'daily_limit_reached', '2026-09-23T04:00:00+00:00'))
        self.assertIsNone(self.review(EVENT_AT + timedelta(hours=8)))
        self.review(EVENT_AT + timedelta(days=1))
        self.assertEqual((self.adjudicate.call_count, self.statuses()), (4, ['evaluated'] * 4))

    def test_discovery_worker_never_claims_review_items(self):
        self.enqueue()
        self.assertEqual(claim_discovery_batch('worker', at=iso(EVENT_AT + SETTLED), policy=DiscoveryPolicy(mode='shadow'),
                                               db_path=self.db), [])


class EvidenceAndChangeTests(EventReviewFixture):
    def test_recorded_review_is_reused_after_a_crash(self):
        self.enqueue()
        self.review()
        with closing(get_connection(self.db)) as conn, conn:
            conn.execute("UPDATE discovery_queue SET status='pending', completion_reason=NULL")
        self.review(EVENT_AT + SETTLED + timedelta(minutes=5))
        self.assertEqual((self.adjudicate.call_count, len(self.records())), (1, 1))


if __name__ == '__main__':
    unittest.main()
