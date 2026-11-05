"""Attribute manager decisions from stored data: no model, no network. Daily closes come from the collector's refresh."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from catalyst_models import timestamp
from market_db_modules.backtesting import daily_closes_after
from market_db_modules.decision_attribution import decisions_needing_attribution, upsert_attribution
from market_db_modules.llm_replay_store import frozen_manager_input
from services.evaluation.decision_attribution import attribute
from services.evaluation.llm_direction_score import CLOSE_LOOKUP_DAYS

LOGGER = logging.getLogger(__name__)


def attribute_decisions(*, limit: int = 500, db_path: Any = None) -> dict[str, int]:
    """Score up to `limit` unsettled decisions, oldest first; returns how many were selected, written and still pending."""
    decisions = decisions_needing_attribution(limit, db_path=db_path)
    written = pending = 0
    for decision in decisions:
        try:
            record = _attribution(decision, db_path)
            upsert_attribution(record, db_path=db_path)
        except Exception:
            LOGGER.exception('Attribution failed for decision %s', decision['id'])
            continue
        written += 1
        pending += record['primary_failure'] == 'pending'
    return {'selected': len(decisions), 'written': written, 'pending': pending}


def _attribution(decision: dict[str, Any], db_path: Any) -> dict[str, Any]:
    decided_at = timestamp(decision['decision_run_at'])
    closes = daily_closes_after(decision['symbol'], decided_at, until=decided_at + timedelta(days=CLOSE_LOOKUP_DAYS),
                                db_path=db_path)
    catalysts = [decision['catalyst_state']] if decision['catalyst_state'] else []
    return attribute(decision, frozen_manager_input(decision) or {}, decision['thesis_check'], catalysts, closes)
