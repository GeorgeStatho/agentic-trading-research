"""Coverage-aware gate, calibration and paired strategy reports with proof citations."""
from contextlib import closing
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from statistics import mean
from zoneinfo import ZoneInfo

from db_common import get_connection
from services.core.pipeline_version import gate_config
from services.core.trading_path import trading_path_hashes
from services.research.research_contract import ResearchPolicy, STRATEGIES, canonical, direction, fingerprint, utc
from services.research.research_promotion import (GATE_BLOCKS, gate_code_hash, policy_hash,
                                                  research_only_candidate, source_hash)
from services.research.research_versions import evaluation_version, require_current_evaluator
from services.research.shadow_portfolio import (HARD_BLOCK_BASIS, MOVE_BASIS, evaluate_gate, evaluate_gate_on_hard_blocks,
                                                evaluate_gate_on_moves, expectancy, proposed_side)

# Gates that judge whether a catalyst deserves an entry are scored on the underlying's move;
# contract and economics gates judge execution, which only a priced option trade can show.
MOVE_SCORED_BLOCKS = {'catalyst', 'continuation', 'regime', 'thesis', 'entry_extension', 'regime_spy_trend'}
# A pre-check is right when what it rejects would have been hard-blocked later anyway.
PRECISION_SCORED_BLOCKS = {'tradability'}
MINIMUM_HARD_BLOCK_PRECISION = 0.95
HARD_BLOCKS = {('EXECUTION', 'premium_per_trade_cap'),
               ('CONTRACT_SELECTION', 'continuation_confirmed_but_no_eligible_contract')}
from .research_runs import read_run
from .research_observations import paired_champion_results, validate_strategy_evaluation


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


def forward_move(conn, candidate_id, at):
    """The candidate's raw 5-day residual move, if it had resolved and been checked by `at`."""
    residual = conn.execute('SELECT * FROM candidate_price_observations WHERE candidate_id=?', (candidate_id,)).fetchone()
    # calibration_table applies the predicted direction itself, so it needs the raw
    # residual. A database this process never initialized may predate that column;
    # report it as unresolved rather than failing the whole report.
    return (raw_residual(residual) if residual and residual['forward_at'] and residual['forward_checked_at']
            and utc(residual['forward_at']) <= utc(at) and utc(residual['forward_checked_at']) <= utc(at) else None)


def hard_block_outcome(conn, candidate_id, at):
    """'blocked' if a later stage refused the pick for the premium cap or no eligible contract, 'fits' if
    an order went out, None while neither is known by `at`."""
    for row in conn.execute('SELECT stage, reason_code, occurred_at FROM candidate_rejections WHERE candidate_id=?',
                            (candidate_id,)):
        if (row['stage'], row['reason_code']) in HARD_BLOCKS and utc(row['occurred_at']) <= utc(at):
            return 'blocked'
    submitted = conn.execute('''SELECT 1 FROM candidate_snapshots WHERE candidate_id=? AND stage='order_submitted'
        AND julianday(captured_at)<=julianday(?)''', (candidate_id, utc(at).isoformat())).fetchone()
    return 'fits' if submitted else None


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


def monthly_report(run_id, month, *, as_of=None, db_path=None):
    # Reject malformed months before they can silently select an empty cohort.
    if month != 'all':
        datetime.strptime(month, '%Y-%m')
    at = utc(as_of or datetime.now(timezone.utc)).isoformat()
    with closing(get_connection(db_path)) as conn, conn:
        run = read_run(conn, run_id)
        rows = []
        observation_results = []
        for saved in conn.execute('SELECT * FROM research_candidates WHERE run_id=?', (run_id,)):
            inputs = json.loads(saved['input_json'])
            if fingerprint(inputs) != saved['input_hash']:
                raise ValueError('Frozen research inputs changed')
            if (month != 'all' and utc(inputs['created_at']).strftime('%Y-%m') != month) or utc(inputs['information_cutoff_time']) > utc(at):
                continue
            positions = {row['strategy']: json.loads(row['result_json']) for row in conn.execute(
                'SELECT * FROM research_shadow_positions WHERE run_id=? AND candidate_id=?', (run_id, saved['candidate_id']))
                if utc(row['outcome_cutoff_time']) <= utc(at)}
            rows.append({'candidate_id': saved['candidate_id'], 'features': inputs['features'],
                         'gates': inputs['features'].get('gates', {}), 'positions': positions,
                         'result': positions.get('counterfactual', {'status': 'uncollected'}),
                         'residual_return': forward_move(conn, saved['candidate_id'], at),
                         'selection_source': inputs['selection_source'],
                         'gate_code': inputs.get('gate_code') or {}})
            evaluation = validate_strategy_evaluation(conn, run, saved, inputs)
            if evaluation:
                position = positions.get('full_v2', {})
                observation_results.append({
                    'observation_id': evaluation['observation_id'], 'candidate_id': saved['candidate_id'],
                    'evaluation_id': evaluation['evaluation_id'],
                    'status': position.get('status', 'uncollected'),
                    'hypothetical_pnl': position.get('hypothetical_pnl'),
                })
        gates = []
        run_sources = run['manifest'].get('source_hashes') or {}
        for stage in sorted({stage for row in rows for stage in row['gates']}):
            code = gate_code_hash(stage, run_sources)
            # Rows enrolled before per-row gate hashes were recorded matched the run's manifest exactly.
            same_code = [row for row in rows if row['gate_code'].get(stage, code) == code]
            evaluation = evaluate_gate(same_code, stage)
            if GATE_BLOCKS.get(stage) in MOVE_SCORED_BLOCKS:
                evaluation = {**evaluate_gate_on_moves(same_code, stage),
                              **{'option_' + key: value for key, value in evaluation.items() if key != 'stage'}}
            evaluation['n_gate_code_changed'] = len(rows) - len(same_code)
            relevant = [row for row in same_code if stage in row['gates']]
            evaluation['regimes'] = sorted({row['features'].get('regime') for row in relevant} - {None, 'unknown'})
            evaluation['production_selection_complete'] = all(row['selection_source'] in {'recorded_production', 'replayed_production_picker'} for row in relevant)
            evaluation['daily_coverage_complete'] = bool(relevant) and all(row['result'].get('verified_daily_marks') for row in relevant)
            block = run['manifest'].get('gate_config', {}).get(GATE_BLOCKS.get(stage))
            evaluation['policy_hash'] = policy_hash(stage, block, code) if block is not None else None
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
        if run.get('evaluation_version'):
            report.update(report_version='research-report-v2', evaluation_version=run['evaluation_version'],
                          observation_results=sorted(observation_results, key=lambda item: item['observation_id']),
                          n_shared_observations=len(observation_results),
                          n_legacy_unpaired=len(rows) - len(observation_results))
        report_id = fingerprint(report)
        report['report_id'] = report_id
        conn.execute('INSERT INTO research_reports VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING',
                     (report_id, run_id, at, month, canonical(report)))
    return report


POOLED_REPORT_VERSION = 'pooled-gate-v1'
# research_reports.month doubles as the report kind; monthly readers select their own month values.
POOLED_REPORT_MONTH = 'pooled'
NEW_YORK = ZoneInfo('America/New_York')


def pooled_gate_report(stage, *, as_of=None, db_path=None):
    """One gate scored on every frozen-forward row its current code and policy produced.

    Rows carry their own gate-code hash, so deploys that leave the gate's files alone keep pooling.
    Only the first ask per company, New York day and proposed side counts, and only asks a whole
    horizon older than the report, so the window is closed and outcomes have had time to resolve.
    Entry gates are scored on the underlying's move, pre-checks on whether their rejections were
    hard-blocked later. The report records the evaluator; promotion refuses one it no longer runs.
    """
    block = GATE_BLOCKS.get(stage)
    precision = block in PRECISION_SCORED_BLOCKS
    if block not in MOVE_SCORED_BLOCKS and not precision:
        raise ValueError('Only gates scored on the underlying move or on later hard blocks pool across runs')
    policy = ResearchPolicy()
    at = utc(as_of or datetime.now(timezone.utc))
    window_end = at - timedelta(days=policy.holding_days)
    live = gate_config().get(block)
    if live is None:
        raise ValueError(f'The current pipeline has no {block} policy to bind a proof to')
    current = policy_hash(stage, live, gate_code_hash(stage, trading_path_hashes()))
    runs, firsts, other, duplicates, unfinished = {}, {}, 0, 0, 0
    with closing(get_connection(db_path)) as conn, conn:
        for saved in conn.execute('''SELECT c.* FROM research_candidates c JOIN research_runs r ON r.run_id=c.run_id
                WHERE r.kind='frozen_forward' ORDER BY julianday(c.information_cutoff_time),c.candidate_id,c.run_id'''):
            if research_only_candidate(saved['candidate_id']) or utc(saved['information_cutoff_time']) > window_end:
                continue
            inputs = json.loads(saved['input_json'])
            if fingerprint(inputs) != saved['input_hash']:
                raise ValueError('Frozen research inputs changed')
            if stage not in inputs['features'].get('gates', {}):
                continue
            # An unfinished candidate's verdicts are only its rejections, which would tilt the sample.
            if inputs.get('selection_source') == 'missing_or_unfinished_snapshots':
                unfinished += 1
                continue
            if saved['run_id'] not in runs:
                runs[saved['run_id']] = read_run(conn, saved['run_id'])
            manifest = runs[saved['run_id']]['manifest']
            # Rows enrolled before per-row gate hashes were recorded matched the run's manifest exactly.
            code = (inputs.get('gate_code') or {}).get(stage) or gate_code_hash(stage, manifest.get('source_hashes') or {})
            settings = manifest.get('gate_config', {}).get(block)
            if settings is None or policy_hash(stage, settings, code) != current:
                other += 1
                continue
            key = (inputs['symbol'], utc(saved['information_cutoff_time']).astimezone(NEW_YORK).date(),
                   proposed_side(inputs['features']))
            if key in firsts:
                duplicates += 1
                continue
            firsts[key] = {'candidate_id': saved['candidate_id'], 'run_id': saved['run_id'],
                           'features': inputs['features'], 'gates': inputs['features']['gates'],
                           **({'outcome': hard_block_outcome(conn, saved['candidate_id'], at)} if precision
                              else {'residual_return': forward_move(conn, saved['candidate_id'], at)})}
        rows = list(firsts.values())
        score = evaluate_gate_on_hard_blocks if precision else evaluate_gate_on_moves
        gate = {**score(rows, stage), 'policy_hash': current,
                'regimes': sorted({row['features'].get('regime') for row in rows} - {None, 'unknown'})}
        report = {'report_version': POOLED_REPORT_VERSION, 'stage': stage, 'information_cutoff_time': at.isoformat(),
                  'window_end': window_end.isoformat(), 'run_ids': sorted({row['run_id'] for row in rows}),
                  'candidate_ids': [row['candidate_id'] for row in rows],
                  'n_other_code_or_policy': other, 'n_duplicate_asks': duplicates, 'n_unfinished': unfinished,
                  'evaluation_version': evaluation_version(),
                  'sample_unit': 'first verdict per symbol, New York day and proposed side', 'gates': [gate]}
        report_id = fingerprint(report)
        report['report_id'] = report_id if rows else None
        if rows:
            # run_id must name one run (research_gate_promotions references this row): the newest sample's run.
            # The report's run_ids lists every run it pooled.
            conn.execute('INSERT INTO research_reports VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING',
                         (report_id, rows[-1]['run_id'], report['information_cutoff_time'], POOLED_REPORT_MONTH,
                          canonical(report)))
    return report


def _stored_report(conn, report_id):
    row = conn.execute('SELECT * FROM research_reports WHERE report_id=?', (report_id,)).fetchone()
    if row is None:
        raise ValueError('A persisted research report is required')
    report = json.loads(row['report_json'])
    if fingerprint({key: value for key, value in report.items() if key != 'report_id'}) != report_id:
        raise ValueError('Research report hash mismatch')
    return row, report


def _pooled_minimum_samples(report):
    """A pooled proof's window must have closed a full horizon before a report time already past."""
    policy = ResearchPolicy()
    at = utc(report['information_cutoff_time'])
    if at > datetime.now(timezone.utc) or utc(report['window_end']) > at - timedelta(days=policy.holding_days):
        raise ValueError('A pooled proof needs a window closed a full horizon before a past report time')
    return policy.minimum_promotion_samples


def _promotion_report(conn, report_id, *, require_source=True):
    """`require_source=False` checks only the evaluator: a gate proof binds its own code via policy_hash."""
    row, report = _stored_report(conn, report_id)
    run = read_run(conn, row['run_id'])
    if require_source and run['code_hash'] != source_hash():
        raise ValueError('Promotion proof implementation is stale')
    require_current_evaluator(run, source_hash())
    if run['kind'] != 'frozen_forward' or run['status'] != 'sealed':
        raise ValueError('Promotion requires a completed frozen-forward enrollment')
    cohort_size = conn.execute('SELECT COUNT(*) FROM research_candidates WHERE run_id=?', (run['run_id'],)).fetchone()[0]
    if report['n_observed'] != cohort_size:
        raise ValueError('Promotion must cover the entire frozen cohort; use --month all')
    return report, run


def promote_gate(report_id, stage, *, db_path=None):
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        report = _stored_report(conn, report_id)[1]
        if report.get('report_version') == POOLED_REPORT_VERSION:
            minimum = _pooled_minimum_samples(report)
            stored, live = report.get('evaluation_version') or {}, evaluation_version()
            if (stored.get('version'), stored.get('evaluator_hash')) != (live['version'], live['evaluator_hash']):
                raise ValueError('Research evaluator changed; re-run report-gate')
        else:
            report, run = _promotion_report(conn, report_id, require_source=False)
            minimum = run['policy'].minimum_promotion_samples
        gate = next((item for item in report['gates'] if item['stage'] == stage), None)
        option_basis = bool(gate) and gate.get('basis') not in (MOVE_BASIS, HARD_BLOCK_BASIS)
        if (not gate or not gate['policy_hash'] or not gate['coverage_complete']
                or (option_basis and not (gate['production_selection_complete'] and gate['daily_coverage_complete']))
                or gate['n_evaluable'] < minimum
                or gate['n_passed'] == 0 or gate['n_rejected'] == 0 or len(gate['regimes']) < 2 or gate['gate_value'] <= 0):
            raise ValueError('Gate lacks complete counterfactual coverage, positive value, sample size or regime diversity')
        if gate.get('basis') == HARD_BLOCK_BASIS and gate['gate_value'] < MINIMUM_HARD_BLOCK_PRECISION:
            raise ValueError(f'Pre-check precision {gate["gate_value"]:.3f} is below {MINIMUM_HARD_BLOCK_PRECISION}')
        conn.execute('INSERT INTO research_gate_promotions VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                     (stage, gate['policy_hash'], report_id, report['information_cutoff_time'], gate['n_evaluable'], gate['gate_value']))
    return {'stage': stage, 'report_id': report_id, 'sample_size': gate['n_evaluable'], 'gate_value': gate['gate_value']}


def promote_champion(report_id, *, incumbent_report_id=None, db_path=None):
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
        incumbent = conn.execute('SELECT run_id,report_id FROM research_champion WHERE singleton=1').fetchone()
        if incumbent and incumbent['run_id'] and incumbent['run_id'] != run['run_id']:
            incumbent_run = read_run(conn, incumbent['run_id'])
            comparison_id = incumbent_report_id or incumbent['report_id']
            comparison_row = conn.execute('SELECT report_json,run_id FROM research_reports WHERE report_id=?',
                                          (comparison_id,)).fetchone()
            if comparison_row is None:
                raise ValueError('A persisted incumbent comparison report is required')
            comparison_run = read_run(conn, comparison_row['run_id'])
            comparison_report = json.loads(comparison_row['report_json'])
            if comparison_run['manifest'] != incumbent_run['manifest']:
                raise ValueError('Comparison report does not evaluate the incumbent manifest')
            if comparison_run['kind'] != 'frozen_forward' or comparison_run['status'] != 'sealed':
                raise ValueError('Incumbent comparison must be a completed forward cohort')
            comparison_count = conn.execute('SELECT COUNT(*) FROM research_candidates WHERE run_id=?',
                                            (comparison_run['run_id'],)).fetchone()[0]
            if comparison_report['n_observed'] != comparison_count:
                raise ValueError('Incumbent comparison must include its entire frozen cohort')
            comparison = paired_champion_results(comparison_run, comparison_report, run, report)
            if sum(comparison) <= 0:
                raise ValueError('Replacement must also outperform the incumbent on the same complete candidate cohort')
        conn.execute('''UPDATE research_champion SET strategy='full_v2',run_id=?,report_id=?,information_cutoff_time=?
                        WHERE singleton=1''', (run['run_id'], report_id, report['information_cutoff_time']))
    return {'champion_run_id': run['run_id'], 'report_id': report_id, 'strategy': 'full_v2'}
