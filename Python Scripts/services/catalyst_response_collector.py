"""Bounded, restartable coverage for all classified catalysts, including rejects."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import logging

from catalyst_models import timestamp
from db_common import get_connection, json_text
from market_db_modules.pricing_research import (
    load_histories, register_response_events, store_history, store_response, pricing_report,
)
from services.price_response import completed_at, measure_response, positive
from services.pricing_policy import PricingPolicy, RESPONSE_HORIZONS
from agent_helpers.market_context.common import REQUEST_HANDLER, yf
from market_db_modules.research_outcomes import research_outcome_symbols
from market_db_modules.research_runs import collect_active_runs
from services.candidate_lifecycle import observe
from services.price_response import residual
from services.regime_history import fetch_regime_history

LOGGER = logging.getLogger(__name__)
EVENT_BUDGET = 250
FORWARD_BUDGET = 250


def fetch_response_history(symbol, timeframe, *, period='1mo'):
    if timeframe == '1d':
        return fetch_regime_history(symbol)
    if yf is None or REQUEST_HANDLER is None:
        raise RuntimeError('No hourly history provider available')
    ticker = REQUEST_HANDLER.run(yf.Ticker, symbol, _context=f'Ticker({symbol})')
    history = REQUEST_HANDLER.run(
        lambda: ticker.history(period=period, interval='1h', auto_adjust=False, prepost=False),
        _context=f'history({symbol},{period},1h)',
    )
    if history is None or history.empty:
        raise RuntimeError(f'No hourly history returned for {symbol}')
    return 'yfinance', [{'bar_at': str(at), 'close': row['Close'], 'open': row.get('Open'),
                         'high': row.get('High'), 'low': row.get('Low'), 'volume': row.get('Volume')}
                        for at, row in history.iterrows()]


def store_fetched_history(symbol, timeframe, fetcher, *, as_of, db_path):
    """Fetch one series and cache only bars completed by `as_of`; errors are reported, not raised."""
    try:
        provider, bars = fetcher(symbol, timeframe)
        valid = []
        for bar in bars:
            end = completed_at({**bar, 'timeframe': timeframe})
            if positive(bar.get('close')) and end and end <= as_of:
                valid.append({**bar, 'bar_at': timestamp(bar['bar_at']).isoformat(), 'available_at': end.isoformat()})
        store_history(symbol, valid, provider=provider, timeframe=timeframe, captured_at=as_of, db_path=db_path)
        return {'provider': provider, 'bars': len(valid), 'status': 'ok' if valid else 'empty'}
    except Exception as exc:
        LOGGER.warning('Catalyst history unavailable for %s %s: %s', symbol, timeframe, exc)
        return {'status': 'error', 'error': str(exc)}


def refresh_universe_history(*, as_of, policy, fetcher, db_path):
    with closing(get_connection(db_path)) as conn, conn:
        universe = {row[0] for row in conn.execute('SELECT ticker FROM catalyst_response_events')}
        benchmarks = {row[0] for row in conn.execute('SELECT sector_symbol FROM catalyst_response_events WHERE sector_symbol IS NOT NULL')}
        universe.update(benchmarks)
        # Research-route thesis outcomes are measured on the same cached history.
        universe.update(research_outcome_symbols(db_path=db_path))
        # Traded underlyings too, so the news-lag report prices publish/entry/exit from hourly bars.
        universe.update(row[0] for row in conn.execute(
            'SELECT DISTINCT underlying_symbol FROM option_trade_executions WHERE underlying_symbol IS NOT NULL'))
        if universe:
            universe.add('SPY')
            benchmarks.add('SPY')
        attempts = {row['symbol']: row['attempted_at'] for row in conn.execute('SELECT * FROM pricing_history_refreshes')}
    eligible = [symbol for symbol in universe if symbol not in attempts or timestamp(attempts[symbol]).date() < as_of.date()]
    # Oldest attempt first prevents a large alphabetical universe starving later names.
    selected = sorted(eligible, key=lambda symbol: (attempts.get(symbol, ''), symbol not in benchmarks, symbol))[:policy.symbol_budget]
    for symbol in selected:
        details = {timeframe: store_fetched_history(symbol, timeframe, fetcher, as_of=as_of, db_path=db_path)
                   for timeframe in ('1d', '1h')}
        with closing(get_connection(db_path)) as conn, conn:
            conn.execute('''INSERT INTO pricing_history_refreshes VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET attempted_at=excluded.attempted_at,
                status=excluded.status, details_json=excluded.details_json''',
                (symbol, as_of.isoformat(), 'ok' if all(d['status'] == 'ok' for d in details.values()) else 'partial_or_missing', json_text(details)))
    return {'universe_symbols': len(universe), 'attempted_symbols': len(selected),
            'deferred_symbols': len(eligible) - len(selected), 'maximum_fetch_calls': 2 * policy.symbol_budget}


def capture_responses(*, as_of, policy, db_path):
    with closing(get_connection(db_path)) as conn, conn:
        events = [dict(row) for row in conn.execute('''SELECT e.* FROM catalyst_response_events e
            LEFT JOIN catalyst_responses r USING(catalyst_id) GROUP BY e.catalyst_id
            HAVING SUM(CASE WHEN r.status='complete' THEN 1 ELSE 0 END) < ?
            ORDER BY COALESCE(MAX(r.recorded_at), ''), e.event_at, e.catalyst_id LIMIT ?''',
            (len(RESPONSE_HORIZONS), EVENT_BUDGET))]
    for event in events:
        histories = load_histories(event['ticker'], event['sector_symbol'], as_of=as_of, db_path=db_path)
        for horizon in RESPONSE_HORIZONS:
            response = measure_response(histories, event_at=event['event_at'], as_of=as_of,
                                        horizon_days=horizon, direction=event['direction'], policy=policy)
            store_response(event, horizon, response, as_of=as_of, db_path=db_path)
    return len(events)


def capture_forward_returns(*, as_of, db_path):
    with closing(get_connection(db_path)) as conn, conn:
        rows = [dict(row) for row in conn.execute('''SELECT * FROM candidate_price_observations
            WHERE forward_at IS NULL AND julianday(observed_at)+5<=julianday(?)
            AND json_extract(snapshot_json, '$.gate.metrics.status')='complete'
            AND json_extract(snapshot_json, '$.direction') IN ('positive', 'negative')
            ORDER BY COALESCE(forward_checked_at, ''), observed_at LIMIT ?''', (as_of.isoformat(), FORWARD_BUDGET))]
    for row in rows:
        with closing(get_connection(db_path)) as conn, conn:
            conn.execute('UPDATE candidate_price_observations SET forward_checked_at=? WHERE candidate_id=?',
                         (as_of.isoformat(), row['candidate_id']))
        payload = json.loads(row['snapshot_json'])
        metrics = payload['gate']['metrics']
        sign = {'positive': 1, 'negative': -1}.get(payload['direction'])
        if metrics.get('status') != 'complete' or not sign:
            continue
        histories = load_histories(payload['ticker'], payload['sector_symbol'], as_of=as_of, db_path=db_path)
        response = measure_response(histories, event_at=row['observed_at'], horizon_days=5,
                                    as_of=as_of, betas=metrics['betas'])
        if response['status'] != 'complete':
            continue
        # Use the exact prices frozen at the decision, even if newer hourly history
        # would now give a different baseline than the original daily proxy.
        move = residual(response['endpoint_prices'], metrics['endpoint_prices'], metrics['betas'])['residual_return']
        # Keep both: the raw residual is the stock's own move, the adjusted one folds in
        # the catalyst direction. Consumers that apply direction themselves need the raw field.
        with closing(get_connection(db_path)) as conn, conn:
            conn.execute('''UPDATE candidate_price_observations
                SET forward_residual_return=?, forward_raw_residual_return=?, forward_at=? WHERE candidate_id=?''',
                         (move * sign, move, response['outcome_at'], row['candidate_id']))


def collect_catalyst_responses(*, as_of=None, policy=None, fetcher=fetch_response_history, refresh=True, db_path=None):
    now = as_of or datetime.now(timezone.utc)
    policy = policy or PricingPolicy.from_env()
    register_response_events(as_of=now, db_path=db_path)
    budget = refresh_universe_history(as_of=now, policy=policy, fetcher=fetcher, db_path=db_path) if refresh else {}
    processed = capture_responses(as_of=now, policy=policy, db_path=db_path)
    capture_forward_returns(as_of=now, db_path=db_path)
    research_runs = observe(collect_active_runs, as_of=now, db_path=db_path)
    return {'collection': {**budget, 'processed_events': processed, 'event_budget': EVENT_BUDGET},
            'research_runs': research_runs,
            **pricing_report(db_path=db_path)}
