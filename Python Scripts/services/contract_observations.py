"""Persist Part 5 comparisons alongside existing immutable candidate checkpoints."""
from datetime import datetime, timezone

from market_db_modules.contract_research import record_observation


def record_contract_observation(context, stage, payload):
    if not context:
        return
    from services.candidate_lifecycle import checkpoint, observe
    checkpoint(context, stage, payload)
    observe(record_observation, context['candidate_id'] + ':' + stage, stage, payload,
            observed_at=datetime.now(timezone.utc).isoformat(), candidate_id=context['candidate_id'])


def record_scenario_selection(result):
    debug = (result.get('recommendation') or {}).get('selection_debug') or {}
    comparison = (debug.get('selector_debug') or {}).get('scenario_comparison') or {
        'status': 'no_strict_hybrid_pool', 'selection_mode': debug.get('selection_mode')}
    record_contract_observation(result.get('candidate_context'), 'SCENARIO_SELECTION', comparison)
