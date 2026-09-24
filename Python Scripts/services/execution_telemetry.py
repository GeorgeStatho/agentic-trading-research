"""Fill telemetry uses broker executions and dated, contemporaneous quotes only."""
from __future__ import annotations

from datetime import datetime, timezone
import math

from db_helpers.market import pending_execution_telemetry, store_execution_telemetry
from services.order_values import normalize_order_summary
from services.outcome_reconciliation import parse_timestamp


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def quote_values(quote):
    quote = quote if isinstance(quote, dict) else {}
    bid, ask = number(quote.get('bid_price')), number(quote.get('ask_price'))
    valid = bid is not None and ask is not None and 0 < bid <= ask
    return bid, ask, (bid + ask) / 2 if valid else None, ask - bid if valid else None


def capture_execution_context(candidate, arrival):
    option = candidate.get('selected_option') or {}
    bid, ask, mid, _ = quote_values(option.get('latest_quote'))
    arrival = arrival if isinstance(arrival, dict) else {}
    _, _, arrival_mid, spread = quote_values(arrival.get('option'))
    market = candidate.get('market_context') or {}
    underlying = market.get('current_stock_price') or market.get('underlying_stock') or market.get('underlying') or {}
    return {'underlying_at_decision': number(underlying.get('price') or underlying.get('latest_trade_price')),
            'option_bid_at_decision': bid, 'option_ask_at_decision': ask, 'option_mid_at_decision': mid,
            'arrival_mid': arrival_mid, 'arrival_spread': spread, 'arrival_snapshot': arrival,
            'decision_quote': option.get('latest_quote'), 'repricing_count': 0}


def fill_metrics(order, baseline, *, observed_at=None, market_snapshot=None):
    order = normalize_order_summary(order)
    filled_qty, price = number(order.get('filled_qty')), number(order.get('filled_avg_price'))
    filled_at, submitted_at = parse_timestamp(order.get('filled_at')), parse_timestamp(order.get('submitted_at'))
    result = {'order_status': order.get('status'), 'filled_qty': filled_qty,
              'fill_observed_at': observed_at or datetime.now(timezone.utc).isoformat(),
              'submitted_price': number(order.get('limit_price'))}
    if order.get('status') in {'canceled', 'expired', 'rejected', 'replaced'}:
        result['cancel_reason'] = order['status']
    if not filled_qty or price is None or price <= 0:
        return result
    result['fill_price'] = price
    if filled_at:
        result['fill_timestamp'] = filled_at.isoformat()
        if submitted_at and filled_at >= submitted_at:
            result['seconds_to_fill'] = (filled_at - submitted_at).total_seconds()
    direction = -1 if order.get('side') == 'sell' else 1
    for reference, metric in (('option_mid_at_decision', 'slippage_vs_mid'), ('arrival_mid', 'slippage_vs_arrival')):
        mid = number(baseline.get(reference))
        if mid is not None and mid > 0:
            result[metric] = direction * (price - mid)
    mid = number(baseline.get('option_mid_at_decision'))
    if mid and mid > 0 and 'slippage_vs_mid' in result:
        result['spread_paid_pct'] = result['slippage_vs_mid'] / mid
    market_snapshot = market_snapshot if isinstance(market_snapshot, dict) else {}
    stock = market_snapshot.get('underlying') or {}
    stock_at = parse_timestamp(stock.get('timestamp'))
    if filled_at and stock_at and abs((stock_at - filled_at).total_seconds()) <= 5:
        result['underlying_at_fill'] = number(stock.get('price'))
        entry = number(baseline.get('underlying_at_decision'))
        if entry and result['underlying_at_fill'] is not None:
            result['underlying_move_while_working'] = result['underlying_at_fill'] / entry - 1
    option = market_snapshot.get('option') or {}
    quote_at = parse_timestamp(option.get('timestamp'))
    arrival_mid = number(baseline.get('arrival_mid'))
    _, _, fill_mid, _ = quote_values(option)
    if filled_at and quote_at and abs((quote_at - filled_at).total_seconds()) <= 5 and arrival_mid and fill_mid:
        result['option_mid_move_while_working'] = fill_mid / arrival_mid - 1
    return result


def reconcile_execution_telemetry(*, gateway, trading_client, db_path=None):
    kwargs = {'db_path': db_path} if db_path is not None else {}
    report = {'updated': 0, 'errors': []}
    for row in pending_execution_telemetry(**kwargs):
        try:
            order = gateway.get_order_status_summary(trading_client, order_id=row['order_id'])
            metrics = fill_metrics(order, row)
            store_execution_telemetry(row['order_id'], metrics, **kwargs)
            report['updated'] += 1
        except Exception as exc:
            report['errors'].append({'order_id': row['order_id'], 'error': str(exc)})
    return report
