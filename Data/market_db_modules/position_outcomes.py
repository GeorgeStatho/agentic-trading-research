"""Position paths keyed by entry order, including durable post-exit observations."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from statistics import median

from db_common import DB_PATH, get_connection, json_text
from .bootstrap import initialize_database


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def _time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except ValueError:
        return None


def ensure_position_outcome(order_id, *, db_path=DB_PATH):
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        row = conn.execute('''SELECT e.*, h.id AS decision_id, t.id AS thesis_id,
            h.closed_at AS decision_closed_at, h.realized_pnl_pct AS decision_realized_pnl_pct, h.exit_reason
            FROM option_trade_executions e
            LEFT JOIN manager_decision_history h ON h.trade_execution_order_id=e.order_id
            LEFT JOIN position_theses t ON t.trade_execution_order_id=e.order_id
            WHERE e.order_id=? AND e.order_side='buy' AND e.filled_qty>0 AND e.fill_timestamp IS NOT NULL''', (order_id,)).fetchone()
        if row is None:
            return None
        raw = json.loads(row['raw_json'] or '{}')
        option = raw.get('selected_option') or {}
        market = raw.get('market_context') or {}
        conn.execute('''INSERT INTO position_outcomes
            (option_symbol, underlying_symbol, candidate_id, thesis_id, manager_decision_history_id,
             entry_order_id, opened_at, entry_option_price, entry_underlying_price, iv_at_entry,
             pipeline_version_json, sector_symbol)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_order_id) DO UPDATE SET
                manager_decision_history_id=COALESCE(excluded.manager_decision_history_id, manager_decision_history_id),
                thesis_id=COALESCE(excluded.thesis_id, thesis_id),
                entry_option_price=excluded.entry_option_price,
                entry_underlying_price=COALESCE(excluded.entry_underlying_price, entry_underlying_price)
            ''', (row['option_symbol'], row['underlying_symbol'], row['candidate_id'], row['thesis_id'], row['decision_id'],
                  order_id, row['fill_timestamp'], row['fill_price'], row['underlying_at_fill'],
                  _number(option.get('implied_volatility') or (option.get('greeks') or {}).get('implied_volatility')),
                  row['pipeline_version_json'], market.get('sector_etf_symbol') or (market.get('sector_etf') or {}).get('ticker')))
        if row['decision_closed_at']:
            close_position_outcome(row['decision_id'], realized_pnl_pct=row['decision_realized_pnl_pct'],
                                   closed_at=row['decision_closed_at'], exit_reason=row['exit_reason'], conn=conn)
        outcome_id = conn.execute('SELECT id FROM position_outcomes WHERE entry_order_id=?', (order_id,)).fetchone()['id']
        from catalyst_schema import stamp_candidate_catalyst
        stamp_candidate_catalyst(conn, 'position_outcomes', outcome_id, row['candidate_id'])
        return outcome_id


def record_position_observation(snapshot, *, observed_at=None, version=None, db_path=DB_PATH):
    initialize_database(db_path=db_path)
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    now = _time(observed_at)
    with get_connection(db_path) as conn:
        rows = conn.execute('''SELECT * FROM position_outcomes WHERE option_symbol=? AND closed_at IS NULL
            ORDER BY opened_at DESC''', (snapshot.get('symbol'),)).fetchall()
        # Several overlapping entry lots cannot be attributed from one aggregate broker mark.
        if len(rows) != 1:
            return False
        row = dict(rows[0])
        opened = _time(row['opened_at'])
        if not now or not opened or now < opened or (_time(row['last_observed_at']) and now <= _time(row['last_observed_at'])):
            return False
        pnl = _number(snapshot.get('broker_unrealized_pl_pct'))
        if pnl is None:
            pnl = _number(snapshot.get('unrealized_pl_pct'))
        spot = _number(snapshot.get('underlying_stock_price'))
        entry = row['entry_underlying_price']
        underlying = (spot / entry - 1) * 100 if spot is not None and entry and entry > 0 else None
        direction = -1 if snapshot.get('contract_type') == 'put' else 1
        minutes = (now - opened).total_seconds() / 60
        values = {'last_observed_at': observed_at, 'observation_count': row['observation_count'] + 1}
        for metric, current in (('option', pnl), ('underlying', underlying * direction if underlying is not None else None)):
            if current is None:
                continue
            for extreme, better in (('mfe', max), ('mae', min)):
                key = extreme + '_' + metric
                old = row[key]
                updated = better(0, current) if old is None else better(old, current)
                values[key] = updated
                if metric == 'option' and (old is None or updated != old):
                    values['time_to_' + extreme + '_minutes'] = minutes
        if snapshot.get('close_submitted'):
            values['primary_exit_reason'] = snapshot.get('exit_reason')
            values['coactive_exit_reasons_json'] = json_text(snapshot.get('coactive_exit_reasons') or [])
            values['exit_pipeline_version_json'] = json_text(version)
        if spot is not None:
            values['underlying_move'] = underlying
        iv = _number(snapshot.get('implied_volatility'))
        if iv is not None and snapshot.get('close_submitted'):
            values['iv_at_exit'] = iv
        conn.execute('UPDATE position_outcomes SET '+', '.join(f'{k}=?' for k in values)+' WHERE id=?',
                     (*values.values(), row['id']))
        conn.execute('''INSERT INTO position_observations
            (position_outcome_id, observed_at, option_pnl_pct, underlying_pnl_pct, snapshot_json)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING''',
            (row['id'], observed_at, pnl, underlying, json_text({'position': snapshot, 'pipeline_version': version})))
        return True


def close_position_outcome(decision_id, *, realized_pnl_pct, closed_at, exit_reason, conn, closing_order_id=None):
    if closing_order_id:
        observation = conn.execute('''SELECT o.snapshot_json FROM position_observations o
            JOIN position_outcomes p ON p.id=o.position_outcome_id WHERE p.manager_decision_history_id=?
            AND json_extract(o.snapshot_json, '$.position.close_order_id')=? ORDER BY o.id DESC LIMIT 1''',
            (decision_id, closing_order_id)).fetchone()
        envelope = json.loads(observation['snapshot_json']) if observation else {}
        position = envelope.get('position') or {}
        conn.execute('''UPDATE position_outcomes SET coactive_exit_reasons_json=?, iv_at_exit=?,
            underlying_at_exit=?, exit_pipeline_version_json=? WHERE manager_decision_history_id=? AND closed_at IS NULL''',
            (json_text(position.get('coactive_exit_reasons') or []), _number(position.get('implied_volatility')),
             _number(position.get('underlying_stock_price')), json_text(envelope.get('pipeline_version')), decision_id))
    conn.execute('''UPDATE position_outcomes SET closed_at=?, realized_pnl_pct=?, primary_exit_reason=?
        WHERE manager_decision_history_id=? AND closed_at IS NULL''',
        (closed_at, realized_pnl_pct, exit_reason, decision_id))


def list_post_exit_positions(*, db_path=DB_PATH):
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        return [dict(row) for row in conn.execute('''SELECT * FROM position_outcomes WHERE closed_at IS NOT NULL
            AND (return_1d_after_exit IS NULL OR return_3d_after_exit IS NULL OR spy_return IS NULL
                 OR residual_move IS NULL OR (sector_symbol IS NOT NULL AND sector_return IS NULL))
                 AND underlying_symbol IS NOT NULL''')]


def resolve_post_exit_returns(position_id, bars, *, checked_on, sessions=None, db_path=DB_PATH):
    """Returns are close-to-close underlying returns, counted in observed exchange sessions.

    Daily provider bars supply the session calendar; never substitute weekdays for holidays.
    No option-price claim is made after expiration.
    """
    with get_connection(db_path) as conn:
        row = conn.execute('SELECT * FROM position_outcomes WHERE id=?', (position_id,)).fetchone()
        if not row or not row['closed_at']:
            return False
        from zoneinfo import ZoneInfo
        exit_date = _time(row['closed_at']).astimezone(ZoneInfo('America/New_York')).date().isoformat()
        closes = {str(bar['session']): float(bar['close']) for bar in bars
                  if _number(bar.get('close')) is not None and float(bar['close']) > 0 and str(bar['session']) < checked_on}
        exit_close = closes.get(exit_date)
        after = sorted({day for day in (sessions if sessions is not None else closes)
                        if exit_date < day < checked_on})
        values = {}
        if exit_close:
            for sessions in (1, 3):
                if len(after) >= sessions and after[sessions-1] in closes:
                    values[f'return_{sessions}d_after_exit'] = (closes[after[sessions-1]] / exit_close - 1) * 100
        if values:
            conn.execute('UPDATE position_outcomes SET '+', '.join(f'{k}=?' for k in values)+' WHERE id=?',
                         (*values.values(), position_id))
        conn.execute('INSERT OR REPLACE INTO post_exit_checks VALUES (?, ?, ?)',
                     (position_id, checked_on, 'resolved' if len(values)==2 else 'waiting_for_sessions'))
        return bool(values)


def exit_attribution_report(*, db_path=DB_PATH):
    initialize_database(db_path=db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute('''SELECT primary_exit_reason, mfe_option, realized_pnl_pct FROM position_outcomes
            WHERE closed_at IS NOT NULL''').fetchall()
    groups = {}
    for row in rows:
        group = groups.setdefault(row['primary_exit_reason'] or 'unknown', {'closed_positions': 0, 'samples': []})
        group['closed_positions'] += 1
        if row['mfe_option'] is not None and row['realized_pnl_pct'] is not None:
            group['samples'].append(row['mfe_option'] - row['realized_pnl_pct'])
    return [{'exit_reason': reason, 'closed_positions': group['closed_positions'],
             'observed_path_count': len(group['samples']),
             'median_mfe_minus_realized_pct': median(group['samples']) if group['samples'] else None}
            for reason, group in groups.items()]


def resolve_position_benchmarks(position_id, histories, *, checked_on, db_path=DB_PATH):
    """Attribute holding-period daily-close returns; preserve the exact source bars.

    Residual is underlying minus sector (or SPY when no sector is mapped), not
    beta-adjusted alpha. These are session-close proxies, never fill-time prices.
    """
    from zoneinfo import ZoneInfo
    with get_connection(db_path) as conn:
        row = conn.execute('SELECT * FROM position_outcomes WHERE id=?', (position_id,)).fetchone()
        if not row or not row['closed_at']:
            return False
        dates = [_time(row[key]).astimezone(ZoneInfo('America/New_York')).date().isoformat()
                 for key in ('opened_at', 'closed_at')]
        if dates[1] >= checked_on:
            return False
        references, returns = {}, {}
        for name, symbol in (('underlying', row['underlying_symbol']), ('spy', 'SPY'), ('sector', row['sector_symbol'])):
            if not symbol or symbol not in histories:
                continue
            history = histories[symbol]
            bars = {bar['session']: bar for bar in history['bars']}
            pair = [bars.get(day) for day in dates]
            if not all(bar and _number(bar.get('close')) is not None and float(bar['close']) > 0 for bar in pair):
                continue
            returns[name] = (float(pair[1]['close']) / float(pair[0]['close']) - 1) * 100
            references[name] = {'symbol': symbol, 'provider': history['provider'], 'entry': pair[0], 'exit': pair[1]}
        values = {}
        for name, column in (('underlying', 'underlying_move'), ('spy', 'spy_return'), ('sector', 'sector_return')):
            if name in returns:
                values[column] = returns[name]
        benchmark = 'sector' if row['sector_symbol'] else 'spy'
        if 'underlying' in returns and benchmark in returns:
            values['residual_move'] = returns['underlying'] - returns[benchmark]
        if not values:
            return False
        values['benchmark_json'] = json_text({'basis': 'entry_session_close_to_exit_session_close',
                                             'residual_benchmark': benchmark, 'references': references})
        conn.execute('UPDATE position_outcomes SET '+', '.join(f'{k}=?' for k in values)+' WHERE id=?',
                     (*values.values(), position_id))
        return True


def position_entry_context(option_symbol, *, db_path=DB_PATH):
    """Only associate exits with an unambiguous entry lifetime."""
    with get_connection(db_path) as conn:
        rows = conn.execute('SELECT candidate_id, pipeline_version_json FROM position_outcomes '
                            'WHERE option_symbol=? AND closed_at IS NULL', (option_symbol,)).fetchall()
    if len(rows) == 1 and rows[0]['candidate_id']:
        return {'candidate_id': rows[0]['candidate_id'], 'pipeline_version': json.loads(rows[0]['pipeline_version_json'] or '{}')}
    return None
