"""Paired incumbent/challenger reports on shared observations, judged by pre-registered criteria.

A pair counts only when both strategies have a resolved outcome. A missing quote, an unevaluated
observation or a pending exit removes the pair and lowers coverage; it never counts as a flat
trade, so missing data cannot look like an avoided loss.
"""
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
import json
import random
from statistics import median
from zoneinfo import ZoneInfo

from db_common import get_connection
from services.forward_evaluation_policy import AcceptancePolicy
from services.research_contract import canonical, fingerprint, utc
from services.research_promotion import source_hash
from services.research_versions import require_current_evaluator
from .bootstrap import initialize_database
from .forward_evaluation import cohort_registrations
from .pricing_research import quantile
from .research_observations import validate_strategy_evaluation
from .research_reports import calibration_table
from .research_runs import ENROLLMENT_GRACE, read_run

REPORT_VERSION = 'forward-comparison-v1'
STRATEGY = 'full_v2'
BASELINES = ('sentiment_volume', 'sentiment_trend', 'taxonomy_expectancy', 'SMH', 'QQQ')
RESOLVED = {'complete', 'cash'}
# Per-candidate placeholder catalysts identify no shared announcement.
UNATTRIBUTED_CATALYST = 'catalyst:unknown:'
NEW_YORK = ZoneInfo('America/New_York')


def enrolled_results(conn, run, as_of):
    """observation_id -> what the run's strategy and baselines did, as known at `as_of`."""
    results = {}
    for saved in conn.execute('SELECT * FROM research_candidates WHERE run_id=?', (run['run_id'],)):
        inputs = json.loads(saved['input_json'])
        if fingerprint(inputs) != saved['input_hash']:
            raise ValueError('Frozen research inputs changed')
        if not inputs.get('observation_id') or utc(inputs['information_cutoff_time']) > utc(as_of):
            continue
        validate_strategy_evaluation(conn, run, saved, inputs)
        positions = {row['strategy']: json.loads(row['result_json']) for row in conn.execute(
            'SELECT * FROM research_shadow_positions WHERE run_id=? AND candidate_id=?',
            (run['run_id'], saved['candidate_id'])) if utc(row['outcome_cutoff_time']) <= utc(as_of)}
        results[inputs['observation_id']] = {'candidate_id': saved['candidate_id'], 'symbol': inputs['symbol'],
                                             'cutoff': inputs['information_cutoff_time'],
                                             'features': inputs['features'], 'positions': positions}
    return results


def pair_status(observation_id, sides, evaluations):
    for role in ('incumbent', 'challenger'):
        side = sides[role]
        if side is None:
            evaluation = evaluations.get((observation_id, role)) or {}
            return f'{role}_not_enrolled:' + (evaluation.get('reason') or evaluation.get('status') or 'not_evaluated')
        status = side['positions'].get(STRATEGY, {}).get('status', 'uncollected')
        if status not in RESOLVED:
            return f'{role}_outcome_{status}'
    return 'resolved'


def max_drawdown(positions):
    """Largest peak-to-trough fall of cumulative P&L, with trades ordered by their exit."""
    exits = sorted((position['hypothetical_exit']['at'], position['hypothetical_pnl'])
                   for position in positions if position['status'] == 'complete')
    peak = total = drawdown = 0.0
    for _, pnl in exits:
        total += pnl
        peak = max(peak, total)
        drawdown = max(drawdown, peak - total)
    return drawdown


def strategy_summary(positions):
    trades = [position for position in positions if position['status'] == 'complete']
    pnl = sum(position['hypothetical_pnl'] for position in positions)
    capital = sum(position['capital'] for position in trades)
    return {'observations': len(positions), 'trades': len(trades), 'cash': len(positions) - len(trades),
            'net_pnl': pnl, 'capital_at_risk': capital, 'net_return': pnl / capital if capital else None,
            'win_rate': sum(position['hypothetical_pnl'] > 0 for position in trades) / len(trades) if trades else None,
            'worst_trade': min((position['hypothetical_pnl'] for position in trades), default=None),
            'max_drawdown': max_drawdown(positions),
            'verified_daily_marks': all(position.get('verified_daily_marks') for position in trades)}


def cluster_labels(pairs):
    """Decisions on the same announcement, or on one company the same day, form one cluster.

    The live pipeline re-evaluates an announcement every cycle while it stays fresh, so its
    repeated decisions are one sample, not many. Market-wide moves shared by unrelated decisions
    on one day are not clustered; the regime floor is the only time-diversity requirement.
    """
    parent = list(range(len(pairs)))

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    groups = defaultdict(list)
    for index, pair in enumerate(pairs):
        if pair.get('catalyst_id') and not pair['catalyst_id'].startswith(UNATTRIBUTED_CATALYST):
            groups[('event', pair['catalyst_id'])].append(index)
        groups[('company_day', pair['symbol'], utc(pair['cutoff']).astimezone(NEW_YORK).date())].append(index)
    for members in groups.values():
        for index in members[1:]:
            parent[root(index)] = root(members[0])
    return [root(index) for index in range(len(pairs))]


def cluster_bootstrap(differences, labels, *, resamples, confidence, seed):
    """One-sided interval for the mean paired difference, resampling whole clusters."""
    if not differences:
        return {'lower_bound': None, 'upper_bound': None, 'resamples': resamples, 'seed': seed}
    groups = defaultdict(list)
    for difference, label in zip(differences, labels):
        groups[label].append(difference)
    keys = sorted(groups)
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        sample = [groups[rng.choice(keys)] for _ in keys]
        means.append(sum(map(sum, sample)) / sum(map(len, sample)))
    return {'lower_bound': quantile(means, 1 - confidence), 'upper_bound': quantile(means, confidence),
            'resamples': resamples, 'seed': seed, 'confidence': confidence}


def acceptance_criteria(acceptance, facts):
    baselines = facts['baselines']
    checks = (
        ('paired_observations', facts['n_paired'] >= acceptance.minimum_paired_observations,
         facts['n_paired'], acceptance.minimum_paired_observations),
        ('independent_clusters', facts['n_clusters'] >= acceptance.minimum_clusters,
         facts['n_clusters'], acceptance.minimum_clusters),
        ('regimes', len(facts['regimes']) >= acceptance.minimum_regimes, facts['regimes'], acceptance.minimum_regimes),
        ('coverage', facts['coverage'] is not None and facts['coverage'] >= acceptance.minimum_coverage,
         facts['coverage'], acceptance.minimum_coverage),
        ('positive_paired_net_pnl', facts['paired_difference'] > 0, facts['paired_difference'], 0),
        ('bootstrap_lower_bound', (facts['bootstrap']['lower_bound'] or 0) > 0,
         facts['bootstrap']['lower_bound'], 0),
        ('beats_every_baseline', bool(baselines) and all(row['n'] and row['difference'] > 0 for row in baselines.values()),
         {name: row['difference'] for name, row in baselines.items()}, 0),
        ('drawdown', facts['challenger']['max_drawdown'] <= acceptance.maximum_drawdown_ratio * facts['incumbent']['max_drawdown'],
         facts['challenger']['max_drawdown'], acceptance.maximum_drawdown_ratio * facts['incumbent']['max_drawdown']),
        ('verified_daily_marks', facts['challenger']['verified_daily_marks'] and facts['incumbent']['verified_daily_marks'],
         None, True),
    )
    return [{'criterion': name, 'passed': bool(passed), 'value': value, 'threshold': threshold}
            for name, passed, value, threshold in checks]


def _cohort_facts(conn, cohort_id, at):
    decisions = [dict(row) for row in conn.execute('SELECT * FROM decision_observations WHERE cohort_id=?', (cohort_id,))]
    evaluations = {(row['observation_id'], row['role']): dict(row) for row in conn.execute(
        'SELECT * FROM paired_evaluations WHERE cohort_id=?', (cohort_id,))}
    residuals, latency = {}, []
    for decision in decisions:
        if decision['status'] != 'recorded':
            continue
        price = conn.execute('SELECT * FROM candidate_price_observations WHERE candidate_id=?',
                             (decision['candidate_id'],)).fetchone()
        if price and price['forward_at'] and utc(price['forward_at']) <= utc(at):
            residuals[decision['observation_id']] = price['forward_raw_residual_return']
        catalyst = conn.execute('SELECT state_json FROM candidate_catalysts WHERE candidate_id=? AND is_primary=1',
                                (decision['candidate_id'],)).fetchone()
        published = json.loads(catalyst['state_json']).get('first_public_time') if catalyst else None
        if published:
            latency.append((utc(decision['recorded_at']) - utc(published)).total_seconds())
    return decisions, evaluations, residuals, latency


def comparison_report(cohort_id, *, as_of=None, db_path=None, persist=True):
    initialize_database(db_path=db_path)
    at = utc(as_of or datetime.now(timezone.utc)).isoformat()
    registrations = cohort_registrations(cohort_id, db_path=db_path)
    if set(registrations) != {'incumbent', 'challenger'}:
        raise ValueError('A comparison needs a cohort with an incumbent and a challenger run')
    if registrations['incumbent']['policy_hash'] != registrations['challenger']['policy_hash']:
        raise ValueError('Incumbent and challenger were registered under different acceptance criteria')
    acceptance = AcceptancePolicy(**registrations['challenger']['policy'])
    with closing(get_connection(db_path)) as conn:
        runs = {role: read_run(conn, registration['run_id']) for role, registration in registrations.items()}
        if runs['incumbent'].get('evaluation_version') != runs['challenger'].get('evaluation_version'):
            raise ValueError('Comparison requires the same evaluator and observation protocol')
        if runs['incumbent']['policy'] != runs['challenger']['policy']:
            raise ValueError('Comparison requires the same evaluation policy')
        results = {role: enrolled_results(conn, run, at) for role, run in runs.items()}
        decisions, evaluations, residuals, latency = _cohort_facts(conn, cohort_id, at)
        catalysts = {row['observation_id']: row['catalyst_id'] for row in conn.execute(
            '''SELECT o.observation_id, o.catalyst_id FROM research_observations o
               JOIN decision_observations d ON d.observation_id=o.observation_id WHERE d.cohort_id=?''', (cohort_id,))}
    observation_ids = sorted({row['observation_id'] for row in decisions if row['status'] == 'recorded'})
    pairs, statuses = [], defaultdict(int)
    for observation_id in observation_ids:
        sides = {role: results[role].get(observation_id) for role in ('incumbent', 'challenger')}
        status = pair_status(observation_id, sides, evaluations)
        statuses[status] += 1
        if status == 'resolved':
            pairs.append({'observation_id': observation_id, 'symbol': sides['challenger']['symbol'],
                          'catalyst_id': catalysts.get(observation_id),
                          'cutoff': sides['challenger']['cutoff'], 'regime': sides['challenger']['features'].get('regime'),
                          'incumbent': sides['incumbent']['positions'],
                          'challenger': sides['challenger']['positions'],
                          'difference': (sides['challenger']['positions'][STRATEGY]['hypothetical_pnl']
                                         - sides['incumbent']['positions'][STRATEGY]['hypothetical_pnl'])})
    labels = cluster_labels(pairs)
    differences = [pair['difference'] for pair in pairs]
    bootstrap = cluster_bootstrap(differences, labels, resamples=acceptance.bootstrap_resamples,
                                  confidence=acceptance.confidence,
                                  seed=int(fingerprint([pair['observation_id'] for pair in pairs])[:12], 16))
    baselines = {}
    for name in BASELINES:
        usable = [pair for pair in pairs if pair['challenger'].get(name, {}).get('status') in RESOLVED]
        baselines[name] = {'n': len(usable), 'difference': sum(
            pair['challenger'][STRATEGY]['hypothetical_pnl'] - pair['challenger'][name]['hypothetical_pnl']
            for pair in usable)}
    total = len(observation_ids) + sum(row['status'] != 'recorded' for row in decisions)
    facts = {'n_paired': len(pairs), 'n_clusters': len(set(labels)),
             'regimes': sorted({pair['regime'] for pair in pairs} - {None, 'unknown'}),
             'coverage': len(pairs) / total if total else None, 'paired_difference': sum(differences),
             'bootstrap': bootstrap, 'baselines': baselines,
             'incumbent': strategy_summary([pair['incumbent'][STRATEGY] for pair in pairs]),
             'challenger': strategy_summary([pair['challenger'][STRATEGY] for pair in pairs])}
    pending = (any(status.endswith('_outcome_pending') or status.endswith('_outcome_uncollected') for status in statuses)
               or any(row['status'] == 'pending' for row in evaluations.values()))
    window_end = registrations['challenger']['window_end']
    final = (utc(at) >= utc(window_end) + ENROLLMENT_GRACE and not pending
             and all(run['status'] == 'sealed' for run in runs.values()))
    criteria = acceptance_criteria(acceptance, facts)
    challenger_calls = [row for (_, role), row in evaluations.items() if role == 'challenger']
    report = {
        'report_version': REPORT_VERSION, 'cohort_id': cohort_id, 'information_cutoff_time': at,
        'window': {'start': registrations['challenger']['window_start'], 'end': window_end},
        'acceptance': {'policy': acceptance.assumptions(), 'policy_hash': registrations['challenger']['policy_hash']},
        'runs': {role: {'run_id': run['run_id'], 'config_hash': run['config_hash'], 'code_hash': run['code_hash'],
                        'status': run['status'], 'manifest_pipeline_version': run['manifest']['pipeline_version']}
                 for role, run in runs.items()},
        'evaluation_version': runs['challenger'].get('evaluation_version'),
        'challenger_definition': (runs['challenger']['manifest'].get('research_comparison') or {}).get('challenger'),
        'observations': {'recorded': len(observation_ids), 'unavailable': total - len(observation_ids),
                         'pair_statuses': dict(sorted(statuses.items()))},
        **{key: facts[key] for key in ('n_paired', 'n_clusters', 'regimes', 'coverage', 'paired_difference',
                                       'bootstrap', 'baselines', 'incumbent', 'challenger')},
        'mean_paired_difference': sum(differences) / len(differences) if differences else None,
        'research_cost': {'challenger_model_calls': sum(row['model_calls'] for row in challenger_calls),
                          'challenger_latency_seconds_median': median(
                              [row['latency_seconds'] for row in challenger_calls if row['latency_seconds'] is not None]
                              or [None])},
        'time_to_decision_seconds_median': median(latency) if latency else None,
        'calibration': {role: calibration_table([{'features': side['features'], 'residual_return': residuals.get(oid)}
                                                 for oid, side in results[role].items()]) for role in results},
        'criteria': criteria,
        'final': final,
        'verdict': 'interim' if not final else 'accepted' if all(row['passed'] for row in criteria) else 'not_accepted',
        'basis': 'one contract per decision at the decision-time ask, exited at the closing bid under the shared '
                 'research exit policy; daily marks only, so intraday claims are not evaluated',
    }
    report_id = 'comparison:' + fingerprint(report)
    report['report_id'] = report_id
    if persist:
        with closing(get_connection(db_path)) as conn, conn:
            conn.execute('INSERT INTO forward_comparison_reports VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                         (report_id, cohort_id, runs['challenger']['run_id'], runs['incumbent']['run_id'], at,
                          report['verdict'], canonical(report)))
    return report


def get_comparison_report(report_id, *, db_path=None):
    with closing(get_connection(db_path)) as conn:
        row = conn.execute('SELECT report_json FROM forward_comparison_reports WHERE report_id=?', (report_id,)).fetchone()
    if row is None:
        raise ValueError('Unknown comparison report')
    report = json.loads(row['report_json'])
    if 'comparison:' + fingerprint({key: value for key, value in report.items() if key != 'report_id'}) != report_id:
        raise ValueError('Comparison report hash mismatch')
    return report


def latest_comparisons(conn, *, limit=5):
    """The newest report per cohort, trimmed for display."""
    rows = conn.execute('''SELECT report_json FROM forward_comparison_reports r WHERE information_cutoff_time=(
        SELECT MAX(information_cutoff_time) FROM forward_comparison_reports WHERE cohort_id=r.cohort_id)
        ORDER BY information_cutoff_time DESC LIMIT ?''', (limit,)).fetchall()
    keys = ('report_id', 'cohort_id', 'information_cutoff_time', 'window', 'challenger_definition', 'verdict', 'final',
            'observations', 'n_paired', 'n_clusters', 'regimes', 'coverage', 'paired_difference',
            'mean_paired_difference', 'bootstrap', 'baselines', 'incumbent', 'challenger', 'criteria',
            'research_cost', 'time_to_decision_seconds_median')
    return [{key: report.get(key) for key in keys} for report in (json.loads(row['report_json']) for row in rows)]


def refresh_comparison_reports(*, as_of=None, db_path=None):
    """One interim report per cohort per day, so evidence is reviewed without anyone asking."""
    at = utc(as_of or datetime.now(timezone.utc))
    with closing(get_connection(db_path)) as conn:
        cohorts = [row['cohort_id'] for row in conn.execute('''SELECT cohort_id FROM research_run_acceptance
            WHERE role='challenger' AND julianday(window_start)<=julianday(?)
            AND cohort_id IN (SELECT cohort_id FROM decision_observations) AND cohort_id NOT IN
            (SELECT cohort_id FROM forward_comparison_reports WHERE verdict!='interim'
             OR date(information_cutoff_time)=date(?)) ORDER BY window_start''', (at.isoformat(), at.isoformat()))]
    results = []
    for cohort_id in cohorts:
        try:
            report = comparison_report(cohort_id, as_of=at, db_path=db_path)
            results.append({'cohort_id': cohort_id, 'report_id': report['report_id'], 'verdict': report['verdict']})
        except ValueError as exc:
            results.append({'cohort_id': cohort_id, 'status': 'unavailable', 'reason': str(exc)})
    return results


def promote_challenger(report_id, *, db_path=None, now=None):
    """Record approval evidence. Production keeps its models until the operator switches them."""
    report = get_comparison_report(report_id, db_path=db_path)
    if report['verdict'] != 'accepted' or not report['final']:
        raise ValueError('Only a final comparison whose pre-registered criteria all passed can be approved')
    registrations = cohort_registrations(report['cohort_id'], db_path=db_path)
    if any(registration['policy_hash'] != report['acceptance']['policy_hash'] for registration in registrations.values()):
        raise ValueError('The cohort acceptance criteria differ from the report')
    current_source = source_hash()
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        for role, summary in report['runs'].items():
            run = read_run(conn, summary['run_id'])
            require_current_evaluator(run, current_source)
            if run['code_hash'] != current_source or run['config_hash'] != summary['config_hash']:
                raise ValueError('Approval evidence no longer matches the current implementation; open a new cohort')
        challenger = report['challenger_definition'] or {}
        approved_at = utc(now or datetime.now(timezone.utc)).isoformat()
        record = {'report_id': report_id, 'cohort_id': report['cohort_id'], 'challenger': challenger,
                  'challenger_manifest': report['runs']['challenger']['manifest_pipeline_version'],
                  'base_pipeline_version': report['runs']['incumbent']['manifest_pipeline_version'],
                  'acceptance_hash': report['acceptance']['policy_hash'],
                  'evaluation_version': report['evaluation_version'], 'approved_at': approved_at,
                  'operator_action': 'set STRATEGIST_MODEL/MANAGER_MODEL to the challenger models and deploy; '
                                     'the switched configuration starts its own forward cohort',
                  'champion_pin': 'unchanged; a pinned production manifest blocks a switched configuration until '
                                  'it is unpinned or re-promoted'}
        approval_id = 'approval:' + fingerprint(record)
        conn.execute('INSERT INTO challenger_approvals VALUES (?,?,?,?,?)',
                     (approval_id, report_id, challenger.get('challenger_id', 'unknown'), approved_at, canonical(record)))
    return {'approval_id': approval_id, **record}
