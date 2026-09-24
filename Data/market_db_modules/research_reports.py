"""Coverage-aware gate, calibration and paired strategy reports with proof citations."""
from contextlib import closing
from collections import defaultdict
from datetime import datetime, timezone
import json
from statistics import mean

from db_common import DB_PATH, get_connection
from services.research_contract import STRATEGIES, canonical, direction, fingerprint, utc
from services.research_promotion import GATE_BLOCKS, policy_hash, source_hash
from services.shadow_portfolio import evaluate_gate, expectancy
from .research_runs import read_run


def calibration_table(rows):
    groups = defaultdict(list)
    for row in rows:
        feature = row['features']
        for component in ('thesis_confidence', 'timing_confidence', 'execution_confidence'):
            key = (feature.get('model', 'unknown'), feature.get('catalyst_family', 'unknown'),
                   feature.get('regime', 'unknown'), component)
            groups[key].append((feature.get('confidence', {}).get(component), row.get('residual_return'),
                                direction(feature.get('manager_direction'))))
    result = []
    for (model, family, regime, component), observations in sorted(groups.items()):
        bins = []
        for level in ('low', 'medium', 'high'):
            seen = [item for item in observations if item[0] == level]
            resolved = [item for item in seen if item[1] is not None and item[2] in {'call', 'put'}]
            bins.append({'bin': level, 'n_seen': len(seen), 'n_resolved': len(resolved),
                         'direction_accuracy': mean((move > 0 if side == 'call' else move < 0) for _, move, side in resolved) if resolved else None,
                         'mean_residual_return': mean(item[1] for item in resolved) if resolved else None})
        values = [item['direction_accuracy'] for item in bins]
        result.append({'model': model, 'catalyst_family': family, 'regime': regime, 'component': component,
                       'bins': bins, 'missing_confidence': sum(item[0] not in {'low', 'medium', 'high'} for item in observations),
                       'monotonic': None if None in values else values == sorted(values),
                       'interpretation': 'ordinal descriptive bins; no probability calibration or sizing authority'})
    return result


def raw_residual(row):
    return row['forward_raw_residual_return'] if 'forward_raw_residual_return' in row.keys() else None


def strategy_table(rows):
    names = (*STRATEGIES, 'SMH', 'QQQ')
    paired = [row for row in rows if all(row['positions'].get(name, {}).get('status') in {'complete', 'cash'} for name in names)]
    result = []
    for name in names:
        positions = [row['positions'].get(name, {}) for row in rows]
        resolved = [position for position in positions if position.get('status') in {'complete', 'cash'}]
        pnl = sum(position['hypothetical_pnl'] for position in resolved)
        trades = sum(position['status'] == 'complete' for position in resolved)
        days = sum(position['deployed_dollar_days'] for position in resolved)
        paired_pnl = sum(row['positions'][name]['hypothetical_pnl'] for row in paired)
        result.append({'strategy': name, 'n_observed': len(rows), 'n_resolved': len(resolved), 'n_trades': trades,
                       'n_missing': len(rows) - len(resolved), 'net_pnl': pnl,
                       'expectancy': expectancy(pnl, trades, len(rows), days),
                       'paired_n': len(paired), 'paired_net_pnl': paired_pnl,
                       'daily_coverage_complete': bool(resolved) and all(position.get('verified_daily_marks') or
                           position['status'] == 'cash' or name in {'SMH', 'QQQ'} for position in resolved),
                       'paired_expectancy_per_candidate': paired_pnl / len(paired) if paired else None})
    result.sort(key=lambda row: (-(row['paired_expectancy_per_candidate'] or 0), row['strategy']))
    for index, row in enumerate(result, 1):
        row['rank'] = index if paired else None
    return result, paired


def monthly_report(run_id, month, *, as_of=None, db_path=DB_PATH):
    # Reject malformed months before they can silently select an empty cohort.
    if month != 'all':
        datetime.strptime(month, '%Y-%m')
    at = utc(as_of or datetime.now(timezone.utc)).isoformat()
    with closing(get_connection(db_path)) as conn, conn:
        run = read_run(conn, run_id)
        rows = []
        for saved in conn.execute('SELECT * FROM research_candidates WHERE run_id=?', (run_id,)):
            inputs = json.loads(saved['input_json'])
            if fingerprint(inputs) != saved['input_hash']:
                raise ValueError('Frozen research inputs changed')
            if (month != 'all' and utc(inputs['created_at']).strftime('%Y-%m') != month) or utc(inputs['information_cutoff_time']) > utc(at):
                continue
            positions = {row['strategy']: json.loads(row['result_json']) for row in conn.execute(
                'SELECT * FROM research_shadow_positions WHERE run_id=? AND candidate_id=?', (run_id, saved['candidate_id']))
                if utc(row['outcome_cutoff_time']) <= utc(at)}
            residual = conn.execute('SELECT * FROM candidate_price_observations WHERE candidate_id=?', (saved['candidate_id'],)).fetchone()
            # calibration_table applies the predicted direction itself, so it needs the raw
            # residual. A database this process never initialized may predate that column;
            # report it as unresolved rather than failing the whole report.
            move = (raw_residual(residual) if residual and residual['forward_at'] and residual['forward_checked_at']
                    and utc(residual['forward_at']) <= utc(at) and utc(residual['forward_checked_at']) <= utc(at) else None)
            rows.append({'candidate_id': saved['candidate_id'], 'features': inputs['features'],
                         'gates': inputs['features'].get('gates', {}), 'positions': positions,
                         'result': positions.get('counterfactual', {'status': 'uncollected'}),
                         'residual_return': move, 'selection_source': inputs['selection_source']})
        gates = []
        for stage in sorted({stage for row in rows for stage in row['gates']}):
            evaluation = evaluate_gate(rows, stage)
            relevant = [row for row in rows if stage in row['gates']]
            evaluation['regimes'] = sorted({row['features'].get('regime') for row in relevant} - {None, 'unknown'})
            evaluation['production_selection_complete'] = all(row['selection_source'] in {'recorded_production', 'replayed_production_picker'} for row in relevant)
            evaluation['daily_coverage_complete'] = bool(relevant) and all(row['result'].get('verified_daily_marks') for row in relevant)
            block = run['manifest'].get('gate_config', {}).get(GATE_BLOCKS.get(stage))
            evaluation['policy_hash'] = policy_hash(stage, block, run['code_hash']) if block is not None else None
            gates.append(evaluation)
        strategies, paired = strategy_table(rows)
        actual = [dict(row) for row in conn.execute('''SELECT p.* FROM position_outcomes p JOIN research_candidates c
            ON c.candidate_id=p.candidate_id WHERE c.run_id=? AND p.closed_at IS NOT NULL''', (run_id,))
            if (month == 'all' or utc(row['opened_at']).strftime('%Y-%m') == month) and utc(row['closed_at']) <= utc(at)]
        report = {'run_id': run_id, 'month': month, 'information_cutoff_time': at, 'n_observed': len(rows),
                  'claim_source': {'kind': run['kind'], 'run_id': run_id, 'config_hash': run['config_hash'],
                                   'code_hash': run['code_hash'], 'frozen_at': run['information_cutoff_time']},
                  'evidence_warning': 'Historical model output may contain parametric future knowledge; frozen forward is stronger.',
                  'gate_attribution': 'one gate at a time; overlapping gate values must not be summed',
                  'return_basis': 'options after spread and fees; benchmark price returns after configured costs',
                  'capital_basis': 'independent one-contract candidate sleeves; no shared portfolio buying-power simulation',
                  'estimate_scope': 'known resolved P&L only; missing outcomes are excluded, never treated as flat trades',
                  'mechanical_feature_coverage': {name: sum(row['features'].get(name) is not None for row in rows)
                      for name in ('independent_root_count', 'evidence_score', 'absorption_ratio', 'iv_percentile', 'relative_volume')},
                  'actual_closed_positions': len(actual), 'gates': gates, 'strategies': strategies,
                  'paired_regimes': sorted({row['features'].get('regime') for row in paired} - {None, 'unknown'}),
                  'calibration': calibration_table(rows)}
        report_id = fingerprint(report)
        report['report_id'] = report_id
        conn.execute('INSERT INTO research_reports VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING',
                     (report_id, run_id, at, month, canonical(report)))
    return report


def _promotion_report(conn, report_id):
    row = conn.execute('SELECT * FROM research_reports WHERE report_id=?', (report_id,)).fetchone()
    if row is None:
        raise ValueError('A persisted research report is required')
    report = json.loads(row['report_json'])
    if fingerprint({key: value for key, value in report.items() if key != 'report_id'}) != report_id:
        raise ValueError('Research report hash mismatch')
    run = read_run(conn, row['run_id'])
    if run['code_hash'] != source_hash():
        raise ValueError('Promotion proof implementation is stale')
    if run['kind'] != 'frozen_forward' or run['status'] != 'sealed':
        raise ValueError('Promotion requires a completed frozen-forward enrollment')
    cohort_size = conn.execute('SELECT COUNT(*) FROM research_candidates WHERE run_id=?', (run['run_id'],)).fetchone()[0]
    if report['n_observed'] != cohort_size:
        raise ValueError('Promotion must cover the entire frozen cohort; use --month all')
    return report, run


def promote_gate(report_id, stage, *, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        report, run = _promotion_report(conn, report_id)
        gate = next((item for item in report['gates'] if item['stage'] == stage), None)
        if (not gate or not gate['policy_hash'] or not gate['coverage_complete']
                or not gate['production_selection_complete'] or not gate['daily_coverage_complete']
                or gate['n_evaluable'] < run['policy'].minimum_promotion_samples
                or gate['n_passed'] == 0 or gate['n_rejected'] == 0 or len(gate['regimes']) < 2 or gate['gate_value'] <= 0):
            raise ValueError('Gate lacks complete counterfactual coverage, positive value, sample size or regime diversity')
        conn.execute('INSERT INTO research_gate_promotions VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                     (stage, gate['policy_hash'], report_id, report['information_cutoff_time'], gate['n_evaluable'], gate['gate_value']))
    return {'stage': stage, 'report_id': report_id, 'sample_size': gate['n_evaluable'], 'gate_value': gate['gate_value']}


def promote_champion(report_id, *, db_path=DB_PATH):
    """Promote a tested production manifest; baseline models remain zero-capital."""
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        report, run = _promotion_report(conn, report_id)
        strategy = next(row for row in report['strategies'] if row['strategy'] == 'full_v2')
        best_other = max(row['paired_net_pnl'] for row in report['strategies'] if row['strategy'] != 'full_v2')
        if (strategy['paired_n'] < run['policy'].minimum_promotion_samples or len(report['paired_regimes']) < 2
                or strategy['paired_net_pnl'] <= max(0, best_other) or strategy['n_missing']
                or strategy['paired_n'] != report['n_observed']
                or not all(row['daily_coverage_complete'] for row in report['strategies'])):
            raise ValueError('Champion needs positive paired outperformance, complete coverage and multiple regimes')
        incumbent = conn.execute('SELECT run_id FROM research_champion WHERE singleton=1').fetchone()
        if incumbent and incumbent['run_id'] and incumbent['run_id'] != run['run_id']:
            comparison = conn.execute('''SELECT old.result_json AS incumbent,new.result_json AS challenger
                FROM research_shadow_positions old JOIN research_shadow_positions new
                ON new.candidate_id=old.candidate_id AND new.strategy=old.strategy
                WHERE old.run_id=? AND new.run_id=? AND old.strategy='full_v2'
                AND old.status IN ('complete','cash') AND new.status IN ('complete','cash')''',
                (incumbent['run_id'], run['run_id'])).fetchall()
            if (len(comparison) != report['n_observed'] or sum(json.loads(row['challenger'])['hypothetical_pnl'] -
                    json.loads(row['incumbent'])['hypothetical_pnl'] for row in comparison) <= 0):
                raise ValueError('Replacement must also outperform the incumbent on the same complete candidate cohort')
        conn.execute('''UPDATE research_champion SET strategy='full_v2',run_id=?,report_id=?,information_cutoff_time=?
                        WHERE singleton=1''', (run['run_id'], report_id, report['information_cutoff_time']))
    return {'champion_run_id': run['run_id'], 'report_id': report_id, 'strategy': 'full_v2'}
