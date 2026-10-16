"""Read-only news-lag report for filled option entries.

For each entry: the lag from the newest cited article's publish time to the order,
how much of the underlying's direction-adjusted move came before entry (publish to
entry) versus after it (entry to exit), and option P&L compared across lag buckets.

Citations are the strategist's validated ``evidence_article_ids``, not every article
the strategist was shown. A cited article stamped after the order (re-scraped feeds
can refresh ``published_at``) is counted but not used as the newest citation.
"""
from __future__ import annotations

from bisect import bisect_right
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import re
from statistics import mean, median

from catalyst_models import timestamp
from db_common import get_connection
from services.price_response import completed_at

LAG_BUCKETS = ('<1h', '1-6h', 'overnight', '>1d')
UNKNOWN_BUCKET = 'unknown'
BUY_SIDES = ('buy', 'orderside.buy')
# Same staleness cap price_response uses for a baseline close.
MAX_PRICE_AGE = timedelta(days=4)
HOURLY_BACKFILL_PERIOD = '730d'  # yfinance's limit for 1h bars
_OPTION_TYPE = re.compile(r'\d{6}([CP])\d{8}$')


def lag_bucket(seconds):
    if seconds is None or seconds < 0:
        return UNKNOWN_BUCKET
    hours = seconds / 3600
    if hours < 1:
        return '<1h'
    if hours < 6:
        return '1-6h'
    return 'overnight' if hours < 24 else '>1d'


def cited_article_ids(manager_input_json):
    try:
        data = json.loads(manager_input_json or '{}')
    except ValueError:
        return []
    payload = data.get('payload', data) if isinstance(data, dict) else {}
    claims = (payload.get('strategist_recommendation') or {}).get('evidence_claims') or []
    return sorted({article_id for claim in claims if isinstance(claim, dict)
                   for article_id in claim.get('evidence_article_ids') or []
                   if isinstance(article_id, int) and article_id > 0})


def _direction(option_symbol, decision):
    match = _OPTION_TYPE.search(str(option_symbol or '').upper())
    kind = {'C': 'call', 'P': 'put'}.get(match.group(1)) if match else str(decision or '').lower()
    return {'call': 1, 'put': -1}.get(kind)


def _positive(value):
    return value if isinstance(value, (int, float)) and value > 0 else None


def _bar_series(conn, symbol):
    """End-time-sorted closes per timeframe, one provider each (never spliced)."""
    by_source = {}
    for row in conn.execute('''SELECT bar_at, timeframe, close_price, provider, raw_json
            FROM historical_underlying_bars WHERE symbol=? AND timeframe IN ('1h', '1d') AND close_price>0''',
                            (symbol,)):
        bar = dict(row)
        bar['available_at'] = json.loads(bar['raw_json'] or '{}').get('available_at')
        end = completed_at(bar)
        if end:
            by_source.setdefault((bar['timeframe'], bar['provider']), []).append((end, bar['close_price']))
    series = {}
    for (timeframe, _), points in by_source.items():
        points.sort()
        current = series.get(timeframe)
        if current is None or (points[-1][0], len(points)) > (current[-1][0], len(current)):
            series[timeframe] = points
    return series


def price_at(series, at):
    """(close, timeframe, bar end) of the last completed bar at or before `at`, hourly first, else daily."""
    if at is None:
        return None, None, None
    for timeframe in ('1h', '1d'):
        points = series.get(timeframe) or []
        index = bisect_right(points, (at, float('inf'))) - 1
        if index >= 0 and at - points[index][0] <= MAX_PRICE_AGE:
            return points[index][1], timeframe, points[index][0]
    return None, None, None


def _move(direction, start, end):
    """Direction-adjusted return; None when both ends resolve to the same bar (no measurement)."""
    if not direction or not start[0] or not end[0] or (start[1] is not None and start[1] == end[1]):
        return None
    return direction * (end[0] / start[0] - 1) + 0.0


def _pct(value):
    return round(value * 100, 4) if value is not None else None


def _trade_row(conn, entry, series_cache):
    missing = []
    order_at = timestamp(entry['submitted_at'] or entry['fill_timestamp'])
    direction = _direction(entry['option_symbol'], entry['manager_decision'] or entry['decision'])
    if entry['decision_id'] is None:
        missing.append('no_decision_link')
    article_ids = cited_article_ids(entry['manager_input_json'])
    if entry['decision_id'] is not None and not article_ids:
        missing.append('no_citations')
    published = {}
    if article_ids:
        placeholders = ','.join('?' * len(article_ids))
        published = {row['id']: timestamp(row['published_at']) for row in conn.execute(
            f'SELECT id, published_at FROM news_articles WHERE id IN ({placeholders})', article_ids)}
    usable = [at for at in published.values() if at and order_at and at <= order_at]
    newest = max(usable, default=None)
    if article_ids and newest is None:
        missing.append('no_publish_time_before_order')
    lag = (order_at - newest).total_seconds() if newest and order_at else None

    symbol = entry['underlying_symbol']
    if symbol not in series_cache:
        series_cache[symbol] = _bar_series(conn, symbol)
    series = series_cache[symbol]
    publish_price, publish_sampling, publish_bar = price_at(series, newest)
    entry_price = _positive(entry['underlying_at_fill']) or _positive(entry['underlying_at_decision'])
    entry_sampling, entry_bar = 'order', None
    if entry_price is None:
        entry_price, entry_sampling, entry_bar = price_at(series, order_at)

    closed_at = entry['outcome_closed_at'] or entry['decision_closed_at']
    pnl = entry['outcome_pnl_pct'] if entry['outcome_pnl_pct'] is not None else entry['decision_pnl_pct']
    closed = closed_at is not None and pnl is not None
    exit_price, exit_sampling, exit_bar = _positive(entry['underlying_at_exit']), 'outcome', None
    if exit_price is None and entry['closing_order_ids_json']:
        closing_ids = [str(order_id) for order_id in json.loads(entry['closing_order_ids_json']) or []]
        if closing_ids:
            row = conn.execute(f'''SELECT underlying_at_decision FROM option_trade_executions
                WHERE order_id IN ({','.join('?' * len(closing_ids))}) AND underlying_at_decision>0
                ORDER BY submitted_at LIMIT 1''', closing_ids).fetchone()
            exit_price, exit_sampling = (row[0], 'closing_order') if row else (None, None)
    if exit_price is None and closed:
        exit_price, exit_sampling, exit_bar = price_at(series, timestamp(closed_at))

    if newest and publish_price is None:
        missing.append('no_publish_price')
    if entry_price is None:
        missing.append('no_entry_price')
    if not closed:
        missing.append('open_or_no_pnl')
    elif exit_price is None:
        missing.append('no_exit_price')
    if direction is None:
        missing.append('no_direction')

    pre = _move(direction, (publish_price, publish_bar), (entry_price, entry_bar))
    post = _move(direction, (entry_price, entry_bar), (exit_price, exit_bar)) if closed else None
    if newest and publish_price and entry_price and pre is None and direction:
        missing.append('publish_and_entry_same_bar')
    if closed and entry_price and exit_price and post is None and direction:
        missing.append('entry_and_exit_same_bar')
    share = None
    if pre is not None and post is not None and (abs(pre) + abs(post)) > 0:
        share = round(abs(pre) / (abs(pre) + abs(post)), 4)
    return {
        'order_id': entry['order_id'], 'symbol': symbol, 'option_symbol': entry['option_symbol'],
        'direction': {1: 'call', -1: 'put'}.get(direction), 'ordered_at': order_at.isoformat() if order_at else None,
        'cited_article_count': len(article_ids),
        'future_dated_citations': sum(1 for at in published.values() if at and order_at and at > order_at),
        'newest_cited_published_at': newest.isoformat() if newest else None,
        'lag_hours': round(lag / 3600, 3) if lag is not None else None, 'lag_bucket': lag_bucket(lag),
        'underlying_at_publish': publish_price, 'underlying_at_entry': entry_price, 'underlying_at_exit': exit_price,
        'price_sampling': {'publish': publish_sampling, 'entry': entry_sampling, 'exit': exit_sampling if exit_price else None},
        'pre_entry_move_pct': _pct(pre), 'post_entry_move_pct': _pct(post), 'pre_entry_share': share,
        'closed': closed, 'closed_at': closed_at, 'option_pnl_pct': round(pnl, 4) if pnl is not None else None,
        # The stock went our way between entry and exit, yet the option still lost.
        'favourable_but_lost': bool(closed and post is not None and post > 0 and pnl < 0),
        'exit_reason': entry['outcome_exit_reason'] or entry['decision_exit_reason'], 'missing': missing,
    }


def _headline(trades):
    lags = [trade['lag_hours'] for trade in trades if trade['lag_hours'] is not None]
    shares = [trade['pre_entry_share'] for trade in trades if trade['closed'] and trade['pre_entry_share'] is not None]
    favourable = [trade for trade in trades if trade['closed'] and (trade['post_entry_move_pct'] or 0) > 0]
    return {
        'entries': len(trades), 'dated_entries': len(lags),
        'median_lag_hours': round(median(lags), 2) if lags else None,
        'within_1h': sum(lag < 1 for lag in lags),
        'median_pre_entry_share': round(median(shares), 4) if shares else None,
        'favourable_closed': len(favourable),
        'favourable_but_lost': sum(trade['favourable_but_lost'] for trade in favourable),
        'measurable': sum(trade['pre_entry_move_pct'] is not None for trade in trades),
    }


def _stats(values):
    values = [value for value in values if value is not None]
    if not values:
        return {'n': 0, 'mean': None, 'median': None}
    return {'n': len(values), 'mean': round(mean(values), 4), 'median': round(median(values), 4)}


def _bucket_summary(rows):
    closed = [row for row in rows if row['closed']]
    pnl = [row['option_pnl_pct'] for row in closed]
    return {
        'trades': len(rows), 'closed': len(closed),
        'option_pnl_pct': {**_stats(pnl), 'win_rate': round(sum(value > 0 for value in pnl) / len(pnl), 4) if pnl else None,
                           'total': round(sum(pnl), 4) if pnl else None},
        'pre_entry_move_pct': _stats([row['pre_entry_move_pct'] for row in rows]),
        'post_entry_move_pct': _stats([row['post_entry_move_pct'] for row in closed]),
        'pre_entry_share': _stats([row['pre_entry_share'] for row in closed]),
        'daily_sampled_publish_prices': sum(row['price_sampling']['publish'] == '1d' for row in rows),
    }


def news_lag_report_from_connection(conn, *, days=0, now=None):
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat() if days else None
    entries = conn.execute('''SELECT e.*, h.id AS decision_id, h.manager_decision,
            h.closed_at AS decision_closed_at, h.realized_pnl_pct AS decision_pnl_pct,
            h.exit_reason AS decision_exit_reason, h.closing_order_ids_json, p.manager_input_json,
            o.closed_at AS outcome_closed_at, o.realized_pnl_pct AS outcome_pnl_pct,
            o.primary_exit_reason AS outcome_exit_reason, o.underlying_at_exit
        FROM option_trade_executions e
        LEFT JOIN manager_decision_history h ON h.trade_execution_order_id=e.order_id
        LEFT JOIN manager_decision_payloads p ON p.manager_decision_history_id=h.id
        LEFT JOIN position_outcomes o ON o.entry_order_id=e.order_id
        WHERE lower(e.order_side) IN (?, ?) AND e.filled_qty>0
          AND (? IS NULL OR julianday(COALESCE(e.submitted_at, e.fill_timestamp))>=julianday(?))
        ORDER BY COALESCE(e.submitted_at, e.fill_timestamp)''', (*BUY_SIDES, since, since)).fetchall()
    series_cache = {}
    trades = [_trade_row(conn, dict(entry), series_cache) for entry in entries]
    coverage = {}
    for trade in trades:
        for reason in trade['missing']:
            coverage[reason] = coverage.get(reason, 0) + 1
    return {
        'as_of': now.isoformat(), 'days': days,
        'definitions': {
            'lag': 'order time minus publish time of the newest cited article published at or before the order',
            'buckets': {'<1h': '< 1h', '1-6h': '1h to < 6h', 'overnight': '6h to < 24h', '>1d': '>= 24h'},
            'moves': 'direction-adjusted underlying % (favourable is positive for calls and puts); '
                     'pre = publish to entry, post = entry to exit; pre_entry_share = |pre| / (|pre| + |post|)',
            'publish_price': "last completed close at or before publish; '1d' sampling uses the prior daily close",
        },
        'headline': _headline(trades),
        'buckets': {bucket: _bucket_summary([trade for trade in trades if trade['lag_bucket'] == bucket])
                    for bucket in (*LAG_BUCKETS, UNKNOWN_BUCKET)},
        'all': _bucket_summary(trades),
        'coverage': {'entries': len(trades), 'missing': dict(sorted(coverage.items())),
                     'future_dated_citations': sum(trade['future_dated_citations'] for trade in trades)},
        'trades': trades,
    }


def news_lag_report(*, days=0, db_path=None, now=None):
    with closing(get_connection(db_path)) as conn:
        return news_lag_report_from_connection(conn, days=days, now=now)


def fetch_missing_history(*, days=0, db_path=None, now=None, fetcher=None):
    """Cache daily and up to ~2 years of hourly bars for traded underlyings (network)."""
    from services.catalyst_response_collector import fetch_response_history, store_fetched_history

    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat() if days else None
    fetcher = fetcher or (lambda symbol, timeframe: fetch_response_history(symbol, timeframe, period=HOURLY_BACKFILL_PERIOD))
    with closing(get_connection(db_path)) as conn:
        symbols = sorted({row[0] for row in conn.execute('''SELECT DISTINCT underlying_symbol FROM option_trade_executions
            WHERE lower(order_side) IN (?, ?) AND filled_qty>0 AND underlying_symbol IS NOT NULL
            AND (? IS NULL OR julianday(COALESCE(submitted_at, fill_timestamp))>=julianday(?))''',
            (*BUY_SIDES, since, since))})
    return {symbol: {timeframe: store_fetched_history(symbol, timeframe, fetcher, as_of=now, db_path=db_path)
                     for timeframe in ('1d', '1h')} for symbol in symbols}
