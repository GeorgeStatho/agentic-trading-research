"""Label catalysts the headline rules left in needs_review, then refresh them so they register."""
from __future__ import annotations

from contextlib import closing
import logging

from agent_helpers.catalyst_classifier import catalyst_inputs
from agent_helpers.llm_client import ModelQuotaExhausted, ModelUnavailable
from agent_helpers.text_utils import build_token_limited_batches
from agent_stages import catalyst_classifier
from catalyst_models import LABEL_VERSION
from db_common import get_connection
from news_db_modules.catalyst_labels import pending_catalysts_for_labeling, record_catalyst_label
from news_db_modules.catalysts import refresh_catalyst
from services.settings import get_settings

LOGGER = logging.getLogger(__name__)
BATCH_SIZE = 20
MAX_UNAVAILABLE_BATCHES_IN_A_ROW = 3
# Only a guard for long bodies: 20 items of at most 1,500 characters fit well inside it.
CONTEXT_LIMIT = 16000
PROMPT_OVERHEAD_TOKENS = 1500


def _batches(inputs):
    for start in range(0, len(inputs), BATCH_SIZE):
        yield from build_token_limited_batches(inputs[start:start + BATCH_SIZE], context_limit=CONTEXT_LIMIT,
                                               prompt_overhead_tokens=PROMPT_OVERHEAD_TOKENS)


def classify_pending_catalysts(*, limit=None, db_path=None) -> dict:
    if limit is None:
        limit = get_settings().catalyst_classifier.max_per_cycle
    summary = dict(selected=0, labeled=0, applied=0, low_confidence=0, invalid=0, quota_stopped=False,
                   unavailable_stopped=False)
    unavailable_in_a_row = 0
    with closing(get_connection(db_path)) as conn:
        pending = pending_catalysts_for_labeling(limit, LABEL_VERSION, conn=conn)
        summary['selected'] = len(pending)
        inputs = catalyst_inputs(pending, conn=conn)
        anchors = {item['catalyst_id']: item['anchor_article_id'] for item in inputs}
        for batch in _batches(inputs):
            try:
                answer, labels, invalid = catalyst_classifier.classify_batch(batch)
            except ModelQuotaExhausted:
                summary['quota_stopped'] = True
                break
            except ModelUnavailable as exc:
                # One failing batch should not cost the rest of the cycle; a model that keeps failing should.
                unavailable_in_a_row += 1
                if unavailable_in_a_row >= MAX_UNAVAILABLE_BATCHES_IN_A_ROW:
                    LOGGER.warning('Catalyst classification stopped after %s unavailable batches in a row: %s',
                                   unavailable_in_a_row, exc)
                    summary['unavailable_stopped'] = True
                    break
                LOGGER.warning('Skipping a catalyst batch; the model is unavailable: %s', exc)
                continue
            unavailable_in_a_row = 0
            summary['invalid'] += invalid
            with conn:  # a batch's labels and the refreshes that apply them commit together
                for label in labels:
                    record_catalyst_label(label['catalyst_id'], label_version=LABEL_VERSION,
                        catalyst_type=label['catalyst_type'], direction=label['direction'],
                        confidence=label['confidence'], anchor_article_id=anchors[label['catalyst_id']],
                        model=answer.model, answered_by=answer.record(), conn=conn)
                    state = refresh_catalyst(label['catalyst_id'], conn=conn)
                    summary['labeled'] += 1
                    summary['low_confidence'] += label['confidence'] == 'low'
                    summary['applied'] += state['classification_source'] == 'rules+model'
    return summary
