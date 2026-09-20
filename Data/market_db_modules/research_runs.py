"""Durable, immutable experiments and repeatable shadow collection."""
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

from db_common import DB_PATH, get_connection
from services.research_contract import (ResearchPolicy, STRATEGIES, canonical, fingerprint,
                                        strategy_decisions, utc)
from services.research_inputs import baseline_contract, load_inputs, replay_missing_selection
from services.research_promotion import source_hash
from services.shadow_portfolio import simulate_option


def migrate_research(conn):
    conn.executescript(Path(__file__).with_name('research_schema.sql').read_text())


def start_run(manifest, *, kind='frozen_forward', target_candidates=200, policy=None,
              db_path=DB_PATH, now=None):
    from .bootstrap import initialize_database
    initialize_database(db_path=db_path)
    policy = policy or ResearchPolicy()
    if kind not in {'frozen_forward', 'leakage_checked_backtest'} or target_candidates < 1:
        raise ValueError('Invalid research run kind or target')
    if not manifest.get('pipeline_version'):
        raise ValueError('A pipeline manifest is required')
    at = utc(now or datetime.now(timezone.utc)).isoformat()
    run_id = str(uuid4())
    code = source_hash()
    config_hash = fingerprint({'manifest': manifest, 'policy': asdict(policy), 'code_hash': code,
                               'kind': kind, 'started_at': at, 'target_candidates': target_candidates})
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute('''INSERT INTO research_runs
            (run_id,kind,started_at,information_cutoff_time,target_candidates,manifest_json,policy_json,config_hash,code_hash)
            VALUES (?,?,?,?,?,?,?,?,?)''',
            (run_id, kind, at, at, target_candidates, canonical(manifest), canonical(asdict(policy)), config_hash, code))
        conn.execute('''INSERT INTO research_champion(singleton,strategy,information_cutoff_time)
                        VALUES (1,'production',?) ON CONFLICT DO NOTHING''', (at,))
    return {'run_id': run_id, 'kind': kind, 'config_hash': config_hash, 'information_cutoff_time': at}


def read_run(conn, run_id):
    row = conn.execute('SELECT * FROM research_runs WHERE run_id=?', (run_id,)).fetchone()
    if row is None:
        raise ValueError('Unknown research run')
    run = dict(row)
    run['manifest'] = json.loads(run['manifest_json'])
    run['policy'] = ResearchPolicy(**json.loads(run['policy_json']))
    expected = fingerprint({'manifest': run['manifest'], 'policy': asdict(run['policy']), 'code_hash': run['code_hash'],
                            'kind': run['kind'], 'started_at': run['started_at'], 'target_candidates': run['target_candidates']})
    if expected != run['config_hash']:
        raise ValueError('Frozen run configuration hash mismatch')
    return run


def _available_quote(row):
    result = dict(row)
    raw = json.loads(result.get('raw_json') or '{}')
    result['available_at'] = raw.get('available_at') or result['created_at']
    return result


def option_quotes(conn, contract, cutoff, as_of):
    symbol = contract.get('symbol') or contract.get('option_symbol')
    rows = [_available_quote(row) for row in conn.execute(
        'SELECT * FROM historical_option_quotes WHERE option_symbol=? ORDER BY julianday(quote_at),provider', (symbol,))]
    # Label data may follow the feature cutoff, but must exist by the report cutoff.
    return [row for row in rows if utc(row['available_at']) <= utc(as_of)
            and (utc(row['quote_at']) > utc(cutoff) or utc(row['available_at']) <= utc(cutoff))]


def _position(conn, inputs, decision, strategy, run, as_of):
    base = {'information_cutoff_time': inputs['information_cutoff_time'], 'outcome_cutoff_time': as_of,
            'hypothetical_entry': None, 'hypothetical_exit': None, 'hypothetical_pnl': None,
            'deployed_dollar_days': None, 'rejected_by': inputs['rejected_by']}
    if decision is None:
        return {**base, 'status': 'missing_decision'}
    if decision == 'skip':
        return {**base, 'status': 'cash', 'hypothetical_pnl': 0, 'deployed_dollar_days': 0}
    contract = inputs['selected_contract'] if strategy in {'counterfactual', 'full_v2'} else baseline_contract(inputs, decision, run['policy'])
    quotes = option_quotes(conn, contract, inputs['information_cutoff_time'], as_of) if contract else []
    from services.price_response import completed_at
    sessions = []
    for row in conn.execute("SELECT * FROM historical_underlying_bars WHERE symbol='SPY' AND timeframe='1d'"):
        bar = dict(row)
        raw = json.loads(bar.get('raw_json') or '{}')
        bar['available_at'] = raw.get('available_at')
        end = completed_at(bar)
        if end and end <= utc(as_of) and utc(raw.get('available_at') or bar['created_at']) <= utc(as_of):
            sessions.append(end)
    return {**base, **simulate_option(contract, quotes, cutoff=inputs['information_cutoff_time'], as_of=as_of, policy=run['policy'], market_sessions=sessions),
            'selection_source': 'recorded_production' if strategy in {'counterfactual', 'full_v2'} else 'baseline_delta_selector'}


def benchmark_position(conn, symbol, reference, inputs, run, as_of):
    """Buy and hold fractional shares for the same capital and holding window."""
    from services.price_response import completed_at
    base = {'information_cutoff_time': inputs['information_cutoff_time'], 'outcome_cutoff_time': as_of,
            'status': 'missing_benchmark_prices', 'hypothetical_pnl': None, 'deployed_dollar_days': None,
            'basis': 'same-capital candidate-window buy-and-hold; price return, not dividend total return'}
    if reference.get('status') != 'complete':
        return {**base, 'status': 'missing_reference_trade'}
    entry_at = utc(reference['hypothetical_entry']['at'])
    exit_at = utc(reference['hypothetical_exit']['at'])
    prices = []
    for row in conn.execute("SELECT * FROM historical_underlying_bars WHERE symbol=? AND timeframe='1d'", (symbol,)):
        row = dict(row)
        raw = json.loads(row.get('raw_json') or '{}')
        row['available_at'] = raw.get('available_at')
        row['research_available_at'] = raw.get('available_at') or row['created_at']
        at = completed_at(row)
        if utc(raw.get('available_at') or row['created_at']) > utc(as_of):
            continue
        if at and at <= utc(as_of) and row['close_price'] and row['close_price'] > 0:
            prices.append((at, row))
    entries = sorted((item for item in prices if item[0] <= entry_at and utc(item[1]['research_available_at']) <= entry_at),
                     key=lambda item: (item[0], item[1]['provider']))
    if not entries or (entry_at - entries[-1][0]).total_seconds() > run['policy'].maximum_mark_gap_days * 86400:
        return base
    first_at, first = entries[-1]
    exits = sorted((item for item in prices if item[0] >= exit_at and item[1]['provider'] == first['provider']), key=lambda item: item[0])
    if not exits or (exits[0][0] - exit_at).total_seconds() > run['policy'].maximum_mark_gap_days * 86400:
        return base
    last_at, last = exits[0]
    capital = reference['capital']
    cost = run['policy'].benchmark_cost_bps / 10000
    pnl = capital * (last['close_price'] / first['close_price'] * (1 - cost) / (1 + cost) - 1)
    return {**base, 'status': 'complete', 'hypothetical_pnl': pnl, 'capital': capital,
            'hypothetical_entry': {'at': entry_at.isoformat(), 'bar_id': first['id'], 'price': first['close_price']},
            'hypothetical_exit': {'at': last_at.isoformat(), 'bar_id': last['id'], 'price': last['close_price']},
            'deployed_dollar_days': capital * (last_at - entry_at).total_seconds() / 86400}


def candidate_finished(conn, candidate_id, as_of):
    terminal = conn.execute('''SELECT 1 FROM candidate_snapshots WHERE candidate_id=?
        AND stage IN ('candidate_complete','admission_blocked','order_submitted')
        AND julianday(captured_at)<=julianday(?)''', (candidate_id, as_of)).fetchone()
    rejected = conn.execute('''SELECT 1 FROM candidate_rejections WHERE candidate_id=? AND shadow=0
        AND (stage='SUPPORT' OR reason_code='stage_error') AND julianday(occurred_at)<=julianday(?)''',
        (candidate_id, as_of)).fetchone()
    return bool(terminal or rejected)


def collect_run(run_id, *, as_of=None, db_path=DB_PATH):
    at = utc(as_of or datetime.now(timezone.utc)).isoformat()
    with closing(get_connection(db_path)) as conn, conn:
        # Serialize enrollment and its N-candidate boundary across collectors.
        conn.execute('BEGIN IMMEDIATE')
        run = read_run(conn, run_id)
        if run['code_hash'] != source_hash():
            raise ValueError('Research implementation changed; open a new challenger')
        count = conn.execute('SELECT COUNT(*) FROM research_candidates WHERE run_id=?', (run_id,)).fetchone()[0]
        skipped_versions = 0
        if run['status'] == 'open':
            candidates = conn.execute('''SELECT * FROM candidates WHERE candidate_id NOT IN
                (SELECT candidate_id FROM research_candidates WHERE run_id=?) ORDER BY julianday(created_at),candidate_id''', (run_id,)).fetchall()
            for candidate in candidates:
                if count >= run['target_candidates']:
                    break
                # Avoid freezing a candidate while its ordinary pipeline is still running.
                if utc(candidate['created_at']) > utc(at) - timedelta(minutes=5):
                    continue
                if run['kind'] == 'frozen_forward':
                    if utc(candidate['created_at']) < utc(run['started_at']):
                        continue
                    if json.loads(candidate['pipeline_version_json']) != run['manifest']:
                        skipped_versions += 1
                        continue
                finished = candidate_finished(conn, candidate['candidate_id'], at)
                if run['kind'] == 'frozen_forward' and not finished and utc(at) - utc(candidate['created_at']) < timedelta(days=1):
                    continue
                inputs = load_inputs(conn, candidate, at) if finished or run['kind'] != 'frozen_forward' else None
                if inputs and run['kind'] == 'frozen_forward':
                    inputs = replay_missing_selection(inputs)
                if inputs is None:
                    rejected_by = [row['stage'] for row in conn.execute('''SELECT DISTINCT stage FROM candidate_rejections
                        WHERE candidate_id=? AND julianday(occurred_at)<=julianday(?) ORDER BY stage''',
                        (candidate['candidate_id'], at))]
                    inputs = {'candidate_id': candidate['candidate_id'], 'symbol': candidate['symbol'],
                              'created_at': candidate['created_at'], 'information_cutoff_time': candidate['created_at'],
                              'features': {'gates': {stage: False for stage in rejected_by}}, 'contracts': [],
                              'selected_contract': None, 'rejected_by': rejected_by,
                              'snapshot_hashes': {}, 'selection_source': 'missing_or_unfinished_snapshots'}
                conn.execute('INSERT INTO research_candidates VALUES (?,?,?,?,?)',
                             (run_id, candidate['candidate_id'], inputs['information_cutoff_time'], fingerprint(inputs), canonical(inputs)))
                count += 1
            if count >= run['target_candidates']:
                conn.execute("UPDATE research_runs SET status='sealed' WHERE run_id=?", (run_id,))
        for row in conn.execute('SELECT * FROM research_candidates WHERE run_id=?', (run_id,)).fetchall():
            inputs = json.loads(row['input_json'])
            if fingerprint(inputs) != row['input_hash']:
                raise ValueError('Frozen research inputs changed')
            decisions = strategy_decisions(inputs['features'], run['policy'])
            from services.research_contract import direction
            decisions['counterfactual'] = direction(inputs['features'].get('manager_direction'))
            results = {}
            for strategy in ('counterfactual', *STRATEGIES, 'SMH', 'QQQ'):
                existing = conn.execute('SELECT * FROM research_shadow_positions WHERE run_id=? AND candidate_id=? AND strategy=?',
                                        (run_id, row['candidate_id'], strategy)).fetchone()
                if existing and (existing['status'] in {'complete', 'cash'} or utc(existing['outcome_cutoff_time']) > utc(at)):
                    results[strategy] = json.loads(existing['result_json'])
                    continue
                result = (benchmark_position(conn, strategy, results['counterfactual'], inputs, run, at)
                          if strategy in {'SMH', 'QQQ'} else _position(conn, inputs, decisions[strategy], strategy, run, at))
                results[strategy] = result
                conn.execute('''INSERT INTO research_shadow_positions VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(run_id,candidate_id,strategy) DO UPDATE SET
                    outcome_cutoff_time=excluded.outcome_cutoff_time,status=excluded.status,result_json=excluded.result_json''',
                    (run_id, row['candidate_id'], strategy, row['information_cutoff_time'], at, result['status'], canonical(result)))
    return {'run_id': run_id, 'candidates': count, 'skipped_manifest_mismatches': skipped_versions,
            'information_cutoff_time': at, 'capital_deployed': 0}


def collect_active_runs(*, as_of=None, db_path=DB_PATH):
    """Called by the existing periodic response collector; stale runs stay frozen."""
    import logging
    with closing(get_connection(db_path)) as conn, conn:
        run_ids = [row[0] for row in conn.execute('''SELECT run_id FROM research_runs
            WHERE status='open' OR EXISTS(SELECT 1 FROM research_shadow_positions p
                WHERE p.run_id=research_runs.run_id AND p.status NOT IN ('complete','cash')) ORDER BY started_at''')]
    results = []
    for run_id in run_ids:
        try:
            results.append(collect_run(run_id, as_of=as_of, db_path=db_path))
        except (ValueError, TypeError) as exc:
            logging.getLogger(__name__).warning('Research run %s paused: %s', run_id, exc)
            results.append({'run_id': run_id, 'status': 'paused', 'reason': str(exc)})
    return results
