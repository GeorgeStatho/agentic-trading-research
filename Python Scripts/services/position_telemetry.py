from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from db_common import DB_PATH, get_connection
from db_helpers.market import ensure_position_outcome, list_post_exit_positions, resolve_post_exit_returns
from services.regime_history import fetch_regime_history
from db_helpers.market import resolve_position_benchmarks


def ensure_tracked_positions(*, db_path=DB_PATH):
    with closing(get_connection(db_path)) as conn, conn:
        orders = conn.execute("SELECT order_id FROM option_trade_executions WHERE order_side='buy' AND filled_qty>0").fetchall()
    for order in orders:
        ensure_position_outcome(order['order_id'], db_path=db_path)


def refresh_post_exit_returns(*, as_of=None, db_path=DB_PATH, fetcher=fetch_regime_history):
    now = as_of or datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    cache, report = {}, {'updated': 0, 'errors': []}
    for position in list_post_exit_positions(db_path=db_path):
        with closing(get_connection(db_path)) as conn, conn:
            if conn.execute('SELECT 1 FROM post_exit_checks WHERE position_outcome_id=? AND checked_on=?',
                            (position['id'], today)).fetchone():
                continue
        symbol = position['underlying_symbol']
        try:
            for ticker in dict.fromkeys([symbol, 'SPY', position.get('sector_symbol')]):
                if not ticker or ticker in cache:
                    continue
                provider, raw_bars = fetcher(ticker)
                cache[ticker] = {'provider': provider, 'bars': [
                    {'session': str(bar['bar_at'])[:10], 'close': bar['close']} for bar in raw_bars]}
            resolve_position_benchmarks(position['id'], cache, checked_on=today, db_path=db_path)
            report['updated'] += int(resolve_post_exit_returns(
                position['id'], cache[symbol]['bars'], checked_on=today,
                sessions=[bar['session'] for bar in cache['SPY']['bars']], db_path=db_path))
        except Exception as exc:
            report['errors'].append({'position_id': position['id'], 'error': str(exc)})
            with closing(get_connection(db_path)) as conn, conn:
                conn.execute('INSERT OR REPLACE INTO post_exit_checks VALUES (?, ?, ?)',
                             (position['id'], today, 'provider_error'))
    return report
