"""Read-only gate ledger: each recorded gate and two plain tags scored on the 5-day residual move.

A gate's value is the promotion proof's: losses avoided minus profits sacrificed on the decisions it rejected,
every move signed by the traded side, so the ledger ranks gates the way `promote_gate` will judge them.
Unknown verdicts are counted but never scored. No model is called and nothing is written.
"""
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from statistics import mean

from db_common import get_connection
from services.gates.entry_gates import ENTRY_GATES
from services.research.shadow_portfolio import MOVE_BASIS, move_gate_value, proposed_side
from .foundation_store import verified_envelope

RESULT_KEY_STAGES = {gate.result_key: gate.name for gate in ENTRY_GATES}


def _decisions(conn, since):
    return conn.execute('''
        SELECT h.id, h.manager_decision, h.candidate_id,
               -- The stored copy of what ENTRY_EXTENSION and the pick score read (prior_five_day_move).
               json_extract(p.manager_input_json, '$.company.trend_quality."5d_return_pct"') AS prior_5d,
               json_extract(p.manager_input_json, '$.replay_metadata.model') AS model,
               json_extract(p.manager_output_json, '$.recommendation.overextension_risk') AS overextension,
               json_extract(p.manager_output_json, '$.market_regime_gate.regime_state') AS regime,
               o.forward_raw_residual_return AS move, json_extract(o.snapshot_json, '$.direction') AS catalyst_direction
        FROM manager_decision_history h JOIN manager_decision_payloads p ON p.manager_decision_history_id = h.id
        LEFT JOIN candidate_price_observations o ON o.candidate_id = h.candidate_id
        WHERE julianday(h.decision_run_at) >= julianday(?) ORDER BY h.id''', (since,)).fetchall()


def _verdicts(conn, decision):
    """{stage label: would_allow (True, False or None)} from the candidate's gate checkpoints and stored verdicts.

    Checkpoints carry unknown verdicts and gates added since; decision_gate_verdicts adds any gate stored
    only there. A gate in both is the same verdict, so the checkpoint wins.
    """
    found = {}
    for row in conn.execute("SELECT * FROM candidate_snapshots WHERE candidate_id=? AND stage=UPPER(stage)",
                            (decision['candidate_id'],)):
        payload = verified_envelope(row, conn)['payload']
        if isinstance(payload, dict) and 'would_allow' in payload:
            found[row['stage']] = (payload.get('policy_version'), payload['would_allow'])
    for row in conn.execute('SELECT gate_name, policy_version, would_allow FROM decision_gate_verdicts '
                            'WHERE manager_decision_history_id=?', (decision['id'],)):
        found.setdefault(RESULT_KEY_STAGES.get(row['gate_name'], row['gate_name']),
                         (row['policy_version'], bool(row['would_allow'])))
    return {f'{stage}[{policy}]' if policy else stage: allowed for stage, (policy, allowed) in found.items()}


def _cell(rows):
    passed = [move for allowed, move in rows if allowed is True]
    rejected = [move for allowed, move in rows if allowed is False]
    unknown = sum(allowed is None for allowed, _ in rows)
    scored = {key: [move for move in moves if move is not None] for key, moves in (('p', passed), ('r', rejected))}
    mean_passed = mean(scored['p']) if scored['p'] else None
    mean_rejected = mean(scored['r']) if scored['r'] else None
    return {'passed': len(passed), 'rejected': len(rejected), 'unknown': unknown, 'unknown_rate': unknown / len(rows),
            'passed_resolved': len(scored['p']), 'rejected_resolved': len(scored['r']),
            'mean_move_passed': mean_passed, 'mean_move_rejected': mean_rejected,
            # No resolved rejection means no evidence either way, not a value of zero.
            **(move_gate_value(scored['r']) if scored['r']
               else {'losses_avoided': None, 'profits_sacrificed': None, 'gate_value': None})}


def _split(rows):
    by = {'by_regime': defaultdict(list), 'by_model': defaultdict(list)}
    for allowed, move, regime, model in rows:
        by['by_regime'][regime].append((allowed, move))
        by['by_model'][model].append((allowed, move))
    return {**_cell([(allowed, move) for allowed, move, *_ in rows]),
            **{name: {key: _cell(group) for key, group in sorted(groups.items())} for name, groups in by.items()}}


def gate_ledger(*, days=60, db_path=None):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    stages = defaultdict(list)
    with closing(get_connection(db_path)) as conn:
        decisions = _decisions(conn, since)
        for decision in decisions:
            side = proposed_side({'manager_direction': decision['manager_decision'],
                                  'catalyst_direction': decision['catalyst_direction']})
            move = None if side is None or decision['move'] is None else decision['move'] * side
            prior = None if side is None or decision['prior_5d'] is None else decision['prior_5d'] * side
            risk = decision['overextension']
            tags = {'overextension_risk_low': None if risk is None else risk == 'low',
                    'prior_5d_move_not_with_trade': None if prior is None else prior <= 0}
            context = (decision['regime'] or 'unknown', decision['model'] or 'unknown')
            for stage, allowed in {**_verdicts(conn, decision), **tags}.items():
                stages[stage].append((allowed, move, *context))
    return {'days': days, 'decisions': len(decisions), 'basis': MOVE_BASIS,
            'move': '5-day residual return signed by the traded side; gate_value = losses avoided minus profits '
                    'sacrificed on rejected decisions, as in the promotion proof',
            'stages': {stage: _split(rows) for stage, rows in sorted(stages.items())}}
