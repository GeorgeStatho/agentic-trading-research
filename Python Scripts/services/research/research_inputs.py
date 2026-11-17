"""Adapt immutable T2 snapshots; never request new model output during replay."""
import json
from statistics import mean

from market_db_modules.foundation_store import verified_envelope
from services.research.research_contract import assert_information_cutoff, direction, publication_lag_hours, utc
from services.gates.entry_gates import ENTRY_GATES
from agent_helpers.deterministic_option_selector import _pick_matching_contract
from agent_helpers.deterministic_option_selector import config
from agent_helpers.deterministic_option_selector.market import selection_date
from services.gates.contract_policy import ContractPolicy
from services.core.pipeline_version import serializable
from services.core.numbers import finite_float


FEATURE_TIME_KEYS = {'timestamp', 'published_at', 'available_at', 'as_of', 'quote_at',
                     'first_public_time', 'latest_root_event_time', 'observed_at',
                     'first_seen_time', 'captured_at', 'recorded_at', 'ingested_at', 'created_at'}


def feature_times(value):
    """Every stated time in a payload. Serializers write an unknown time as "", which states nothing."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FEATURE_TIME_KEYS and item not in (None, ''):
                yield item
            elif isinstance(item, (dict, list)):
                yield from feature_times(item)
    elif isinstance(value, list):
        for item in value:
            yield from feature_times(item)


def load_inputs(conn, candidate, as_of):
    snapshots = {}
    for row in conn.execute('SELECT * FROM candidate_snapshots WHERE candidate_id=? ORDER BY id',
                            (candidate['candidate_id'],)):
        if utc(row['captured_at']) > utc(as_of):
            continue
        envelope = verified_envelope(row, conn)
        if (row['pipeline_version'] != json.loads(candidate['pipeline_version_json'])['pipeline_version']
                or envelope.get('information_cutoff_time') != row['information_cutoff_time']):
            raise ValueError('Snapshot manifest or cutoff does not match its envelope')
        assert_information_cutoff([row['information_cutoff_time']], row['captured_at'])
        snapshots[row['stage']] = (dict(row), envelope['payload'])
    if not snapshots:
        return None
    stages = ('post_contract_selection', 'pre_contract_selection', 'post_manager', 'pre_gates', 'candidate_created')
    stage = next((name for name in stages if name in snapshots), next(iter(snapshots)))
    source, payload = snapshots[stage]
    cutoff = max((row['information_cutoff_time'] for row, _ in snapshots.values()), key=utc)
    # Verdicts may follow contract selection. They are evaluation labels, not
    # inputs to the selection that they are testing.
    assert_information_cutoff(feature_times(payload), source['information_cutoff_time'])
    assert_information_cutoff([cutoff], as_of)
    original = snapshots.get('pre_gates', (None, {}))[1]
    if 'pre_gates' in snapshots:
        assert_information_cutoff(feature_times(original), snapshots['pre_gates'][0]['information_cutoff_time'])
    manager = original.get('recommendation') or payload.get('recommendation') or {}
    market = payload.get('market_context') or original.get('market_context') or {}
    thesis = (payload.get('candidate_context') or snapshots.get('candidate_created', (None, {}))[1])
    catalyst = (thesis.get('catalysts') or [{}])[0]
    seen = catalyst.get('first_seen_time')
    # The cutoff check above already refuses a first-seen time after the cutoff.
    catalyst_age_hours = (utc(cutoff) - utc(seen)).total_seconds() / 3600 if seen else None
    underlying = market.get('underlying_price_history') or {}
    indicators = underlying.get('technical_indicators') or {}
    technical = underlying.get('technical_summary') or {}
    rollup = (original.get('payload') or {}).get('opportunist_rollup') or {}
    distribution = market.get('_v2_scenario_distribution') or {}
    returns = distribution.get('returns') or []
    usable = len(returns) >= distribution.get('minimum_samples', 30)
    gates = {}
    for name, (_, observation) in snapshots.items():
        if isinstance(observation, dict) and 'would_allow' in observation:
            gates[name] = observation['would_allow']
    rejections = [dict(row) for row in conn.execute(
        'SELECT * FROM candidate_rejections WHERE candidate_id=? ORDER BY id', (candidate['candidate_id'],))
        if utc(row['occurred_at']) <= utc(as_of)]
    for rejection in rejections:
        if rejection['stage'] != 'LIQUIDITY':
            gates[rejection['stage']] = False
    # Canonical entry_gate snapshots exist even when record_gate was not reached.
    for entry_gate in ENTRY_GATES:
        verdict = entry_gate.verdict(payload) or {}
        if isinstance(verdict.get('would_allow'), bool):
            gates.setdefault(entry_gate.name, verdict['would_allow'])
    strategist = payload.get('strategist_recommendation') or snapshots.get('post_strategist', (None, {}))[1].get('recommendation') or {}
    confidence = {key: manager.get(key) for key in ('thesis_confidence', 'timing_confidence', 'execution_confidence')}
    selected = payload.get('selected_option')
    liquidity = snapshots.get('LIQUIDITY', (None, []))[1]
    if isinstance(liquidity, list) and selected:
        matches = [row for row in liquidity if row.get('option_symbol') == selected.get('symbol')]
        if matches:
            gates['LIQUIDITY'] = matches[0].get('would_allow')
            if gates['LIQUIDITY'] is False and any(row.get('would_allow') is True for row in liquidity):
                # Filtering could choose a different contract instead of holding cash.
                # Do not award saved-loss dollars without evaluating that replacement.
                gates['LIQUIDITY'] = None
    features = {
        'sentiment': (rollup.get('company') or {}).get('dominant_direction') or rollup.get('sentiment'),
        'relative_volume': (indicators.get('relative_volume') or {}).get('relative_volume_20d'),
        'trend': technical.get('trend_state'), 'historical_expectancy': mean(returns) if usable else None,
        'catalyst_direction': catalyst.get('direction'), 'catalyst_family': catalyst.get('catalyst_family', 'unknown'),
        'catalyst_age_hours': catalyst_age_hours,
        'publication_lag_hours': publication_lag_hours(catalyst),
        'independent_root_count': catalyst.get('independent_root_count'), 'evidence_score': catalyst.get('evidence_score'),
        'absorption_ratio': (snapshots.get('PRICE_ABSORPTION', (None, {}))[1].get('metrics') or {}).get('price_absorption_ratio'),
        'iv_percentile': ((market.get('option_market') or {}).get('volatility_summary') or {}).get('atm_iv_percentile'),
        'strategist_direction': strategist.get('preferred_option_direction') or strategist.get('decision'),
        'manager_direction': manager.get('decision'), 'confidence': confidence,
        'model': payload.get('manager_model') or 'unknown',
        'regime': (market.get('market_regime') or {}).get('state', 'unknown'), 'gates': gates,
        'production_allowed': (bool(selected) and not any(not r['shadow'] and r['stage'] != 'LIQUIDITY' for r in rejections)
                               and not any(isinstance(value, dict) and value.get('allowed') is False
                                           for _, value in snapshots.values()))
                              if 'post_contract_selection' in snapshots or any(not r['shadow'] for r in rejections) else None,
    }
    contracts = (market.get('option_market') or {}).get('contracts') or []
    # The full market context is hundreds of kilobytes; only replaying a missing selection reads it.
    replayable = not selected and direction(manager.get('decision')) in {'call', 'put'}
    return {'candidate_id': candidate['candidate_id'], 'symbol': candidate['symbol'],
            'created_at': candidate['created_at'], 'information_cutoff_time': cutoff,
            'evaluation_cutoff_time': as_of, 'features': features, 'contracts': contracts,
            'selected_contract': selected, 'rejected_by': sorted({r['stage'] for r in rejections}),
            'snapshot_hashes': {name: row['snapshot_hash'] for name, (row, _) in snapshots.items()},
            'selection_context': market if replayable else {}, 'target_dte_bucket': manager.get('target_dte_bucket', 'none'),
            'selection_source': 'recorded_production' if selected else 'unavailable',
            # The run freezes the whole manifest; repeating it per candidate cost ~37 KB each.
            'pipeline_version': json.loads(candidate['pipeline_version_json'])['pipeline_version']}


def replay_missing_selection(inputs, manifest):
    """Run only the deterministic picker, with saved features and the original date.

    Caller verifies the frozen manifest (the candidate's, which a frozen-forward run requires to be
    its own) and source hashes first. No model, quote, account or history refresh belongs here.
    """
    side = direction(inputs['features'].get('manager_direction'))
    if inputs['selected_contract'] or not inputs['contracts'] or side not in {'call', 'put'}:
        return inputs
    current_config = {key: serializable(value) for key, value in vars(config).items()
                      if key.isupper() and isinstance(value, (str, int, float, bool, dict, list, tuple, set, frozenset))}
    if (current_config != manifest.get('selector_config') or
            serializable(ContractPolicy.from_env()) != manifest.get('gate_config', {}).get('contracts')):
        raise ValueError('Selector configuration changed; open a new challenger')
    with selection_date(utc(inputs['information_cutoff_time'])):
        selected, debug = _pick_matching_contract(decision=side, option_contracts=inputs['contracts'],
                                                  market_context=inputs['selection_context'],
                                                  target_dte_bucket=inputs['target_dte_bucket'])
    return {**inputs, 'selected_contract': selected, 'replay_selection_debug': debug,
            'selection_source': 'replayed_production_picker' if selected else 'unavailable'}


def baseline_contract(inputs, decision, policy):
    """Frozen comparison selector: closest absolute delta to .50, then spread.

    This is not represented as a replay of the production selector. In particular
    it cannot supply promotion evidence for a missing production selection.
    """
    candidates = []
    for contract in inputs['contracts']:
        if direction(contract.get('contract_type')) != decision:
            continue
        expiry = contract.get('expiration_date')
        delta = finite_float((contract.get('greeks') or {}).get('delta'))
        quote = contract.get('latest_quote') or {}
        bid, ask = finite_float(quote.get('bid_price')), finite_float(quote.get('ask_price'))
        if not expiry or delta is None or bid is None or ask is None or not 0 <= bid <= ask or ask <= 0:
            continue
        if (utc(expiry) - utc(inputs['information_cutoff_time'])).total_seconds() < policy.holding_days * 86400:
            continue
        candidates.append((abs(abs(delta) - .5), (ask - bid) / ask, str(contract.get('symbol')), contract))
    return min(candidates, key=lambda row: row[:3])[-1] if candidates else None
