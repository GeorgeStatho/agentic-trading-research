"""Finalize one strategy's model outputs on a frozen observation, exactly as of its cutoff.

The incumbent's recorded outputs and a challenger's fresh ones pass through the same
deterministic steps: the continuation gate, contract selection from the observation's frozen
bucket chain, the regime gate, the evidence-thesis gate and, only when enforced, trade economics.
Snapshots stay compact; the shared inputs live once in the observation.
"""
import copy

from services.runtime.candidate_lifecycle import checkpoint, record_gate, reject
from services.evaluation.evaluation_clock import evaluated_at
from services.gates.evidence_continuation_gate import evaluate_evidence_continuation_gate
from services.thesis.evidence_thesis import evaluate_evidence_thesis_gate
from services.gates.market_regime_gate import apply_market_regime_gate_to_manager_result
from services.options.option_dte_buckets import normalize_target_dte_bucket
from services.research.research_contract import assert_information_cutoff
from services.research.research_inputs import feature_times
from services.options.scenario_selector import prepare_scenario_inputs
from services.gates.trade_economics import apply_trade_economics

DIRECTIONAL = {'call', 'put'}


def selection_market_context(source, manager_payload, recommendation):
    """The manager's market context with its bucket's contracts taken from the frozen chains.

    Like production, a non-directional decision or a bucket of `none` keeps the pre-decision chain.
    """
    market = copy.deepcopy(manager_payload.get('market_context') or {})
    bucket = normalize_target_dte_bucket(recommendation.get('target_dte_bucket'))
    buckets = (source.get('option_chain') or {}).get('buckets') or {}
    if recommendation.get('decision') in DIRECTIONAL and bucket in buckets:
        option_market = dict(market.get('option_market') or {})
        option_market.update(contracts=copy.deepcopy(buckets[bucket]), contract_count=len(buckets[bucket]),
                             frozen_bucket=bucket)
        market['option_market'] = option_market
    return market


def compact_market_context(market):
    option_market = market.get('option_market') or {}
    history = market.get('underlying_price_history') or {}
    return {'market_regime': market.get('market_regime'),
            'option_market': {key: option_market.get(key) for key in
                              ('contracts', 'volatility_summary', 'reference_stock_price', 'frozen_bucket')},
            'underlying_price_history': {key: history.get(key) for key in ('technical_indicators', 'technical_summary')},
            '_v2_scenario_distribution': market.get('_v2_scenario_distribution')}


def finalize_paired_decision(context, observation, manager_payload, recommendation, *, strategist_recommendation,
                             model, thesis_policy, economics_policy):
    """Write the candidate's decision snapshots and gate verdicts; return its selection payload."""
    cutoff = observation['information_cutoff_time']
    market = selection_market_context(observation['source_snapshot'], manager_payload, recommendation)
    result = {'company': manager_payload.get('company'), 'candidate_context': context,
              'candidate_id': context['candidate_id'], 'market_context': market,
              'strategist_recommendation': manager_payload.get('strategist_recommendation') or {},
              'recommendation': recommendation, 'selected_option': None}
    checkpoint(context, 'post_strategist', {'recommendation': strategist_recommendation})
    checkpoint(context, 'pre_gates', {'recommendation': recommendation,
                                      'payload': {'opportunist_rollup': manager_payload.get('opportunist_rollup')}})
    with evaluated_at(cutoff):
        result['entry_gate'] = evaluate_evidence_continuation_gate(
            recommendation=recommendation, payload=manager_payload, market_context=market)
        record_gate(context, 'CONTINUATION', result['entry_gate'])
        result = prepare_scenario_inputs(result)
        result = apply_deterministic_option_selection(result)
        result = apply_market_regime_gate_to_manager_result(result)
        record_gate(context, 'REGIME', result.get('market_regime_gate'))
        thesis_gate = evaluate_evidence_thesis_gate(result['strategist_recommendation'], recommendation.get('decision'),
                                                    policy=thesis_policy)
        if thesis_gate is not None:
            result['evidence_thesis_gate'] = thesis_gate
            record_gate(context, 'EVIDENCE_THESIS', thesis_gate)
        # Shadow economics cannot change a decision, so it is replayed only once it is enforced.
        if economics_policy.mode == 'enforce':
            result = apply_trade_economics(result, policy=economics_policy)
            record_gate(context, 'TRADE_ECONOMICS', result.get('trade_economics_gate'))
    decision = (result.get('recommendation') or {}).get('decision')
    if not result.get('selected_option'):
        reject(context, 'CONTRACT_SELECTION',
               (result.get('recommendation') or {}).get('selected_option_source') or 'no_contract',
               metrics={'decision': decision})
    if decision not in DIRECTIONAL:
        reject(context, 'MANAGER', 'non_directional_decision', metrics={'decision': decision})
    payload = {'recommendation': result['recommendation'], 'selected_option': result.get('selected_option'),
               'strategist_recommendation': result['strategist_recommendation'],
               'candidate_context': {'catalysts': context['catalysts']}, 'manager_model': model,
               'market_context': compact_market_context(result['market_context'])}
    # A later timestamp here would let future information into the decision being evaluated.
    assert_information_cutoff(feature_times(payload), cutoff)
    checkpoint(context, 'post_contract_selection', payload)
    checkpoint(context, 'candidate_complete', {'status': 'evaluated'})
    return payload


def apply_deterministic_option_selection(result):
    from agent_helpers.deterministic_option_selector import apply_deterministic_option_selection as select
    return select(result)
