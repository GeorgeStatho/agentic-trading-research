"""Observational pipeline adapters for Part 4; all pricing verdicts are shadow."""
from datetime import datetime, timezone

from catalyst_models import timestamp
from db_common import DB_PATH
from market_db_modules.candidate_lifecycle import record_candidate_snapshot, record_candidate_rejection
from market_db_modules.pricing_research import catalyst_stats, load_histories, record_price_observation, candidate_sector_symbol
from services.candidate_lifecycle import GateResult
from services.option_hurdle_gate import evaluate_option_hurdle
from services.price_response import evaluate_price_absorption
from services.pricing_policy import HORIZON_DAYS, PricingPolicy


def persist_gate(context, gate, *, as_of, db_path=DB_PATH):
    record_candidate_snapshot(context, gate['stage'], gate, cutoff=as_of.isoformat(), db_path=db_path)
    if gate['would_allow'] is False:
        record_candidate_rejection(context, GateResult(False, gate['stage'], gate['reason_codes'][0],
                                                      gate['metrics'], gate['thresholds']), shadow=True, db_path=db_path)


def record_candidate_price_response(context, *, db_path=DB_PATH):
    policy = PricingPolicy.from_env()
    as_of = timestamp(context['created_at'])
    state = context['catalysts'][0]
    stats = catalyst_stats(state['catalyst_family'], HORIZON_DAYS.get(state.get('expected_horizon'), 5),
                           as_of=as_of, minimum_samples=policy.minimum_samples,
                           exclude_catalyst_id=state['catalyst_id'], db_path=db_path)
    histories = load_histories(context['symbol'], candidate_sector_symbol(context['symbol'], db_path=db_path), as_of=as_of,
                              captured_before=as_of.isoformat(), db_path=db_path)
    gate = evaluate_price_absorption(state, histories, stats, as_of=as_of, policy=policy)
    persist_gate(context, gate, as_of=as_of, db_path=db_path)
    record_price_observation(context, gate, db_path=db_path)
    return gate


def apply_option_hurdle_to_manager_result(manager_result, *, as_of=None, db_path=DB_PATH):
    context = manager_result.get('candidate_context')
    if not context:
        return manager_result
    policy = PricingPolicy.from_env()
    now = as_of or datetime.now(timezone.utc)
    state = context['catalysts'][0]
    horizon = HORIZON_DAYS.get(state.get('expected_horizon'), 5)
    stats = catalyst_stats(state['catalyst_family'], horizon, as_of=now,
                           minimum_samples=policy.minimum_samples, exclude_catalyst_id=state['catalyst_id'], db_path=db_path)
    market = manager_result.get('market_context') or {}
    from agent_helpers.deterministic_option_selector.market import _get_reference_stock_price
    option_market = market.get('option_market') or {}
    contracts = option_market.get('pricing_contracts', option_market.get('contracts')) or []
    gate = evaluate_option_hurdle(contracts,
                                 spot=_get_reference_stock_price(market), stats=stats, horizon_days=horizon,
                                 as_of=now, policy=policy)
    persist_gate(context, gate, as_of=now, db_path=db_path)
    return {**manager_result, 'option_hurdle_gate': gate,
            'price_absorption_gate': context.get('price_absorption_gate')}
