"""Event-triggered thesis reviews for open positions. Shadow only.

A material event for a company with an open position (a discovery `position_review` item,
settled for the debounce window) gets a thesis review without waiting for the daily schedule.
It reuses the daily validator's evidence pack, adjudicator and arbiter, records what changed
against the previous verdict, and never executes: the arbiter's action is stored as would-be,
whatever THESIS_VALIDATOR_MODE says. Reviews run after mechanical position management, and a
failure only defers the queue item.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from catalyst_models import timestamp
from market_db_modules.discovery_queue import claim_position_reviews, finish_discovery_item
from market_db_modules.thesis_validator import (
    count_thesis_validator_exits_for_date,
    list_open_position_theses_for_symbol,
)
from market_db_modules.thesis_event_reviews import (
    find_thesis_event_review,
    latest_thesis_verdict,
    record_thesis_event_review,
    thesis_event_reviews,
)
from market_db_modules.evidence_thesis import latest_strategist_thesis_check
from market_db_modules.foundation_store import digest
from market_db_modules.thesis_event_reviews import thesis_event_review_id
from services.common import safe_float
from services.discovery_policy import DiscoveryPolicy
from services.event_thesis_review_policy import EventThesisReviewPolicy
from services.thesis_arbiter import ARBITER_POLICY_VERSION, decide_thesis_arbiter_action
from services.thesis_validator_service import ThesisValidatorSettings, rules_exit_active


LOGGER = logging.getLogger(__name__)
REVIEW_VERSION = 'thesis-event-review-v1'
EVENT_PACK_VERSION = 'thesis-event-evidence-pack-v1'
MARKET_TIMEZONE = ZoneInfo('America/New_York')
SUMMARY_MAX_CHARS = 700
REVIEW_REASON = ('A new event arrived for this company. The articles listed in review_trigger.trigger_article_ids '
                 'are new since entry. Judge whether the original entry thesis still holds in light of them.')
EVALUATED = 'evaluated'


class ReviewUnavailable(RuntimeError):
    """No usable verdict this attempt, e.g. every model sample failed; the item is retried."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def market_day(at: str) -> str:
    return datetime.fromisoformat(at).astimezone(MARKET_TIMEZONE).date().isoformat()


def evidence_hash(event: dict[str, Any]) -> str:
    """Content of the event's evidence; a reverted revision with identical evidence hashes the same."""
    return digest(event.get('signature') or {'roots': event.get('root_event_ids'), 'package': event.get('package_id')})


def new_articles_since(event: dict[str, Any], opened_at: datetime | None) -> list[dict[str, Any]]:
    """Event articles this system first saw after entry; older coverage was part of the entry thesis."""
    articles = []
    for article in event.get('articles') or []:
        seen = timestamp(article.get('first_seen_time') or article.get('event_time'))
        if opened_at is None or (seen is not None and seen > opened_at):
            articles.append(article)
    return articles


def primary_evidence_is_new(event: dict[str, Any], opened_at: datetime | None) -> bool:
    published = timestamp(event.get('published_at'))
    return bool(event.get('package_id')) and (opened_at is None or (published is not None and published > opened_at))


def article_entry(article: dict[str, Any]) -> dict[str, Any]:
    """The daily pack's article shape; the adjudicator renders text only inside untrusted blocks."""
    text = ' '.join(str(article.get('summary') or article.get('body') or '').split())
    return {'article_id': article.get('id'), 'source': str(article.get('source') or '').strip(),
            'published_at': str(article.get('event_time') or article.get('published_at') or '').strip(),
            'title': ' '.join(str(article.get('title') or '').split()), 'summary': text[:SUMMARY_MAX_CHARS]}


def structured_entry_thesis(thesis: dict[str, Any], *, db_path=None) -> dict[str, Any] | None:
    """Phase 4's checked thesis for the entry decision: observable invalidation conditions, not a template."""
    check = latest_strategist_thesis_check(thesis.get('candidate_id'), db_path=db_path)
    entry = (check or {}).get('thesis')
    if not entry:
        return None
    return {'evaluation_id': check['evaluation_id'], 'check_status': check['status'],
            'direction': entry.get('direction'), 'horizon': entry.get('horizon'),
            'causal_mechanism': entry.get('causal_mechanism'),
            'invalidation_conditions': entry.get('invalidation_conditions') or []}


def change_summary(previous: dict[str, Any] | None, verdict: dict[str, Any], trigger_ids: list[int]) -> dict[str, Any]:
    """What changed against the latest daily or event verdict, and which new evidence was cited."""
    cited = sorted(set(verdict.get('evidence_ids') or []) & set(trigger_ids))
    if previous is None:
        return {'baseline': None, 'changed': None, 'cited_trigger_article_ids': cited}
    return {'baseline': previous, 'cited_trigger_article_ids': cited,
            'thesis_state': {'before': previous['thesis_state'], 'after': verdict.get('thesis_state')},
            'would_open_today': {'before': previous['would_open_today'], 'after': verdict.get('would_open_today')},
            'changed': (previous['thesis_state'] != verdict.get('thesis_state')
                        or previous['would_open_today'] != verdict.get('would_open_today'))}


class EventThesisReviewService:
    """Review open theses when a settled position-review event arrives; never place an order."""

    def __init__(
        self,
        *,
        policy: EventThesisReviewPolicy | None = None,
        validator_settings: ThesisValidatorSettings | None = None,
        discovery_policy: DiscoveryPolicy | None = None,
        adjudicate: Callable[..., dict[str, Any]] | None = None,
        build_pack: Callable[..., dict[str, Any]] | None = None,
        clock: Callable[[], datetime] = utc_now,
        logger: logging.Logger | None = None,
    ) -> None:
        self._policy = policy or EventThesisReviewPolicy.from_env()
        self._validator = validator_settings or ThesisValidatorSettings.from_env()
        self._discovery_policy = discovery_policy or DiscoveryPolicy.from_env()
        self._adjudicate = adjudicate
        self._build_pack = build_pack
        self._clock = clock
        self._logger = logger or LOGGER

    @property
    def policy(self) -> EventThesisReviewPolicy:
        return self._policy

    def _now(self) -> str:
        return self._clock().astimezone(timezone.utc).isoformat()

    def run(self, *, positions: list[dict[str, Any]], db_path=None) -> dict[str, Any] | None:
        """Review at most `items_per_cycle` settled events; None when there is nothing to do."""
        if self._policy.mode == 'off':
            return None
        owner = 'event-thesis-review:' + uuid4().hex
        items = claim_position_reviews(owner, at=self._now(), policy=self._discovery_policy,
                                       debounce_seconds=self._policy.debounce_seconds,
                                       limit=self._policy.items_per_cycle, db_path=db_path)
        if not items:
            return None
        open_positions = {str(position.get('symbol') or '').strip().upper(): position
                          for position in positions if isinstance(position, dict)}
        return {'mode': self._policy.mode, 'policy_version': self._policy.policy_version,
                'items': [self._review_item(item, open_positions, db_path=db_path) for item in items]}

    def _finish(self, item, *, status, reason, db_path):
        try:
            finish_discovery_item(item['queue_id'], item['lease_token'], status=status, reason=reason,
                                  at=self._now(), policy=self._discovery_policy, db_path=db_path)
        except ValueError:
            # A superseding revision or a reclaimed lease belongs to the next worker.
            self._logger.warning('Event review lease for %s was lost before completion', item['queue_id'])

    def _review_item(self, item, open_positions, *, db_path):
        symbol = str(((item.get('event') or {}).get('company') or {}).get('symbol') or '').upper()
        try:
            theses = list_open_position_theses_for_symbol(symbol, db_path=db_path)
            reviews = [self._review_thesis(thesis, item, open_positions.get(str(thesis['option_symbol']).upper()),
                                           db_path=db_path) for thesis in theses]
        except ReviewUnavailable as exc:
            self._logger.warning('Event thesis review for %s deferred: %s', symbol, exc)
            self._finish(item, status='deferred', reason='review_unavailable', db_path=db_path)
            return {'queue_id': item['queue_id'], 'symbol': symbol, 'error': 'review_unavailable'}
        except Exception as exc:
            self._logger.exception('Event thesis review failed for %s (%s)', symbol, item['queue_id'])
            self._finish(item, status='deferred', reason='review_error:' + type(exc).__name__, db_path=db_path)
            return {'queue_id': item['queue_id'], 'symbol': symbol, 'error': type(exc).__name__}
        statuses = sorted({review['status'] for review in reviews})
        self._finish(item, status='completed', reason='reviewed:' + ','.join(statuses) if reviews else 'no_open_thesis',
                     db_path=db_path)
        return {'queue_id': item['queue_id'], 'symbol': symbol, 'reviews': reviews}

    def _review_thesis(self, thesis, item, position, *, db_path):
        existing = find_thesis_event_review(thesis_event_review_id(thesis['id'], item['queue_id']), db_path=db_path)
        if existing:
            return self._summary(existing)
        event = item['event']
        reviewed_at = self._now()
        opened_at = timestamp(thesis.get('opened_at'))
        review = {'schema_version': REVIEW_VERSION, 'policy': self._policy.manifest(), 'authority': 'shadow_only',
                  'executed': False, 'thesis_id': int(thesis['id']), 'option_symbol': str(thesis['option_symbol']).upper(),
                  'symbol': thesis.get('symbol'), 'company_id': item['company_id'], 'queue_id': item['queue_id'],
                  'catalyst_id': item['catalyst_id'], 'evidence_revision': item['evidence_revision'],
                  'change_kind': event.get('change_kind'), 'event_published_at': event.get('published_at'),
                  'evidence_hash': evidence_hash(event), 'reviewed_at': reviewed_at, 'review_day': market_day(reviewed_at)}
        new_articles = new_articles_since(event, opened_at)
        skipped = self._skip(review, position, new_articles, primary_evidence_is_new(event, opened_at), db_path=db_path)
        if skipped:
            return self._record({**review, **skipped}, db_path=db_path)
        return self._record(self._evaluate(review, thesis, position, event, new_articles, db_path=db_path), db_path=db_path)

    def _skip(self, review, position, new_articles, primary_new, *, db_path):
        """Reasons not to spend a model call, checked in order of precedence."""
        if position is None:
            return {'status': 'position_not_open'}
        if rules_exit_active(position):
            return {'status': 'rules_exit_precedence'}
        if str(position.get('pending_order_id') or '').strip():
            return {'status': 'active_exit_order', 'pending_order_id': position['pending_order_id']}
        if not new_articles and not primary_new:
            return {'status': 'no_new_evidence_since_entry'}
        earlier = [record for record in thesis_event_reviews(review['thesis_id'], db_path=db_path)
                   if record['status'] == EVALUATED]
        same_evidence = next((record for record in earlier if record['evidence_hash'] == review['evidence_hash']), None)
        if same_evidence:
            return {'status': 'coalesced', 'coalesced_with': same_evidence['review_id']}
        if sum(record['review_day'] == review['review_day'] for record in earlier) >= self._policy.daily_reviews_per_position:
            # The scheduled daily review still considers this evidence.
            return {'status': 'daily_limit_reached'}
        return None

    def _evaluate(self, review, thesis, position, event, new_articles, *, db_path):
        previous = latest_thesis_verdict(review['thesis_id'], db_path=db_path)
        pack = self._event_pack(thesis, position, event, new_articles, previous,
                                structured_entry_thesis(thesis, db_path=db_path))
        verdict = self._adjudicator()(pack, model=self._validator.model or None, samples=self._validator.samples,
                                      temperature=self._validator.temperature)
        if verdict.get('consistency') == 'error':
            raise ReviewUnavailable('no usable adjudicator sample')
        arbiter = decide_thesis_arbiter_action(
            rules_exit_active=False, verdict=verdict, quantity=int(safe_float(position.get('quantity')) or 0),
            llm_exits_today=count_thesis_validator_exits_for_date(review['review_day'], db_path=db_path),
            max_daily_llm_exits=self._validator.max_daily_llm_exits, kill_switch=self._validator.kill_switch)
        trigger_ids = pack['review_trigger']['trigger_article_ids']
        return {**review, 'status': EVALUATED, 'evidence_pack': pack,
                **{key: verdict.get(key) for key in ('thesis_state', 'would_open_today', 'consistency', 'flip_detected',
                                                     'grounded', 'evidence_ids', 'rationale', 'confidence',
                                                     'sample_count', 'raw_samples', 'prompt_version', 'model')},
                'would_be_action': arbiter['action'], 'arbiter_reason': arbiter['reason'],
                'arbiter_policy_version': ARBITER_POLICY_VERSION, 'mark_mid_price': safe_float(position.get('mid_price')),
                'previous_verdict': previous, 'change': change_summary(previous, verdict, trigger_ids)}

    def _event_pack(self, thesis, position, event, new_articles, previous, structured):
        base = self._pack_builder()(thesis, position, news_limit=self._validator.news_limit)
        trigger = [article_entry(article) for article in new_articles]
        trigger_ids = [entry['article_id'] for entry in trigger]
        catalyst = event.get('catalyst') or {}
        return {**base, 'version': EVENT_PACK_VERSION, 'base_version': base.get('version'),
                'review_reason': REVIEW_REASON,
                'review_trigger': {'catalyst_id': event.get('catalyst_id'), 'catalyst_family': catalyst.get('catalyst_family'),
                                   'catalyst_type': catalyst.get('catalyst_type'), 'direction': catalyst.get('direction'),
                                   'change_kind': event.get('change_kind'), 'event_published_at': event.get('published_at'),
                                   'trigger_article_ids': trigger_ids,
                                   'primary_evidence_package_id': event.get('package_id')},
                'previous_verdict': ({key: previous[key] for key in ('source', 'reviewed_at', 'thesis_state', 'would_open_today')}
                                     if previous else None),
                'structured_entry_thesis': structured,
                'news_since_entry': trigger + [entry for entry in base.get('news_since_entry') or []
                                               if entry.get('article_id') not in trigger_ids],
                'allowed_evidence_article_ids': sorted(set(base.get('allowed_evidence_article_ids') or []) | set(trigger_ids))}

    def _adjudicator(self):
        if self._adjudicate is None:
            from services.thesis_adjudicator import adjudicate_thesis
            self._adjudicate = adjudicate_thesis
        return self._adjudicate

    def _pack_builder(self):
        if self._build_pack is None:
            from services.thesis_evidence_pack import build_thesis_evidence_pack
            self._build_pack = build_thesis_evidence_pack
        return self._build_pack

    def _record(self, review, *, db_path):
        record_thesis_event_review(review, db_path=db_path)
        return self._summary(review)

    @staticmethod
    def _summary(review):
        return {key: review.get(key) for key in ('thesis_id', 'option_symbol', 'status', 'thesis_state', 'would_open_today',
                                                 'would_be_action', 'arbiter_reason', 'coalesced_with')
                if review.get(key) is not None} | {'changed': (review.get('change') or {}).get('changed')}
