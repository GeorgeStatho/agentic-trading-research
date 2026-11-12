"""Positions held by trading accounts. In v1 only follower accounts write rows."""
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json

from db_common import get_connection
from services.trading.account_strategies import Skip
from services.research.research_contract import utc
from services.trading.trading_accounts import PRIMARY_NAME, PRIMARY_STRATEGY

ACTIVE = ('pending_entry', 'open', 'closing')
COPY = 'copy'
PREMIUM_CAP_BLOCKED = 'premium_cap_blocked'


def _update(row_id, db_path, sql, *values):
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute(f'UPDATE account_positions SET {sql} WHERE id=?', (*values, row_id))


def record_plan(account, *, source_account, source_order_id, source_option_symbol, underlying, decision,
                budget, plan, spot=None, source_delta=None, entry_kind=COPY, db_path=None):
    """Record one account's response to a source entry; None when it was already recorded."""
    skipped = isinstance(plan, Skip)
    values = {
        'account': account.name, 'role': account.role, 'strategy': account.strategy,
        'exit_policy': account.exit_policy, 'entry_kind': entry_kind, 'source_account': source_account,
        'source_order_id': source_order_id, 'source_option_symbol': source_option_symbol,
        'underlying': underlying, 'decision': decision, 'budget': budget,
        'status': 'skipped' if skipped else 'pending_entry',
        'skip_reason': plan.reason if skipped else None,
        'legs_json': '[]' if skipped else json.dumps([asdict(leg) for leg in plan.legs]),
        'qty': None if skipped else plan.qty, 'multiplier': None if skipped else plan.multiplier,
        'unit_cost': None if skipped else plan.unit_cost,
        'entry_spot': spot, 'entry_delta': None if skipped else plan.delta, 'source_delta': source_delta,
    }
    with closing(get_connection(db_path)) as conn, conn:
        cursor = conn.execute(
            f'''INSERT INTO account_positions ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})
                ON CONFLICT(account, source_order_id) DO NOTHING''', tuple(values.values()))
        return cursor.lastrowid if cursor.rowcount else None


def mark_entry_order(row_id, *, order_id, db_path=None):
    _update(row_id, db_path, 'entry_order_id=?', order_id)


def mark_failed(row_id, *, error, db_path=None):
    _update(row_id, db_path, "status='failed', error=?", error)


def mark_entry_fill(row_id, *, fill_price, filled_at, qty=None, db_path=None):
    """Open the position; `qty` records a partial fill that differs from the plan."""
    _update(row_id, db_path, "status='open', entry_fill_price=?, entry_at=?, qty=COALESCE(?, qty)",
            fill_price, filled_at, qty)


def mark_partial_order(row_id, *, order_id, db_path=None):
    _update(row_id, db_path, 'partial_order_id=?', order_id)


def mark_partial_fill(row_id, *, qty, fill_price, db_path=None):
    """The slice a partial exit sold leaves the position: its quantity is what the position still holds."""
    _update(row_id, db_path, 'partial_qty=?, partial_fill_price=?, qty=qty-?', qty, fill_price, qty)


def mark_source_seen(row_id, *, at, db_path=None):
    _update(row_id, db_path, 'source_seen_at=?', at)


def mark_closing(row_id, *, order_id, reason, db_path=None):
    _update(row_id, db_path, "status='closing', exit_order_id=?, exit_reason=?", order_id, reason)


def reopen(row_id, *, error, qty=None, db_path=None):
    """Back to open after a failed exit; `qty` is what remains after a partial close."""
    _update(row_id, db_path, "status='open', exit_order_id=NULL, exit_reason=NULL, error=?, qty=COALESCE(?, qty)",
            error, qty)


def mark_closed(row_id, *, fill_price, closed_at, db_path=None):
    _update(row_id, db_path, "status='closed', exit_fill_price=?, exit_at=?", fill_price, closed_at)


def _decoded(row):
    position = dict(row)
    position['legs'] = json.loads(position.pop('legs_json'))
    return position


def active_positions(db_path=None):
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute(f'''SELECT * FROM account_positions WHERE status IN ({', '.join('?' * len(ACTIVE))})
            ORDER BY id''', ACTIVE).fetchall()
    return [_decoded(row) for row in rows]


def closed_source_orders(order_ids, db_path=None):
    """Source entries the primary's own bookkeeping has already recorded as closed."""
    order_ids = list(order_ids)
    if not order_ids:
        return set()
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute(f'''SELECT trade_execution_order_id FROM manager_decision_history
            WHERE closed_at IS NOT NULL AND trade_execution_order_id IN ({', '.join('?' * len(order_ids))})''',
                            order_ids).fetchall()
    return {row[0] for row in rows}


def _trade_pnl(position):
    """Dollar P&L and capital of a closed position; a sold stock leg is a short."""
    legs = position['legs']
    sign = -1 if len(legs) == 1 and legs[0]['asset'] == 'stock' and legs[0]['side'] == 'sell' else 1
    entry, multiplier, trimmed = position['entry_fill_price'], position['multiplier'], position['partial_qty'] or 0
    pnl = (position['exit_fill_price'] - entry) * position['qty'] * multiplier
    pnl += ((position['partial_fill_price'] or 0) - entry) * trimmed * multiplier
    return sign * pnl, entry * (position['qty'] + trimmed) * multiplier


def _mean(values):
    return sum(values) / len(values) if values else None


def _production_dollars(row):
    """Production's (P&L, capital) on a mirrored entry: its realized return on what it actually paid
    (fill price x filled quantity), falling back to the premium every follower was given as its budget."""
    capital = row['source_cost'] or row['budget']
    if row['source_pnl_pct'] is None or not capital:
        return None
    return capital * row['source_pnl_pct'] / 100, capital


def _on_capital_pct(pnl, capital):
    return pnl / capital * 100 if capital else None


def _per_exposure_pct(pnl, delta, units, multiplier, spot):
    """P&L per dollar of underlying exposure at entry. Return on capital mostly measures leverage
    when holds are short; this compares vehicles on the stock move they captured."""
    exposure = abs(delta or 0) * (units or 0) * (multiplier or 0) * (spot or 0)
    return pnl / exposure * 100 if exposure else None


def _trade_per_exposure_pct(row, pnl):
    return _per_exposure_pct(pnl, row['entry_delta'], row['qty'], row['multiplier'], row['entry_spot'])


def _production_per_exposure_pct(row):
    """Production's, per contract: its realized return on its fill price, over its delta at our entry spot."""
    if row['source_pnl_pct'] is None or not row['source_fill_price']:
        return None
    return _per_exposure_pct(row['source_pnl_pct'] / 100 * row['source_fill_price'], row['source_delta'], 1, 1,
                             row['entry_spot'])


def account_summary(*, since=None, entry_kind=COPY, db_path=None):
    """Per-account results, plus the primary's own result on the entries the followers mirrored.

    Production is compared two ways on each follower's closed entries: the plain average of its
    trade returns, and its total P&L over total capital (dollar-weighted, see _production_dollars).
    `entry_kind` keeps copied entries (default) apart from the picks production skipped for the premium cap.
    """
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute('''SELECT p.*, (SELECT AVG(h.realized_pnl_pct) FROM manager_decision_history h
                WHERE h.trade_execution_order_id=p.source_order_id) AS source_pnl_pct,
                e.fill_price * e.filled_qty * 100 AS source_cost, e.fill_price AS source_fill_price
            FROM account_positions p LEFT JOIN option_trade_executions e ON e.order_id=p.source_order_id
            WHERE (? IS NULL OR datetime(p.created_at) >= datetime(?)) AND p.entry_kind=? ORDER BY p.id''',
                            (since, since, entry_kind)).fetchall()
    summary, source_pnl, source_dollars, source_exposure, source_open = {}, {}, {}, {}, set()
    for row in map(_decoded, rows):
        if row['source_pnl_pct'] is None:
            source_open.add(row['source_order_id'])
        entry = summary.setdefault(row['account'], {
            'strategy': row['strategy'], 'role': row['role'], 'closed': 0, 'active': 0, 'failed': 0,
            'skipped': {}, 'wins': 0, 'pnl': 0.0, 'capital': 0.0, 'pnl_pcts': [], 'source_pcts': [],
            'source_pnl': 0.0, 'source_capital': 0.0, 'exposure_pcts': [], 'source_exposure_pcts': []})
        production = _production_dollars(row)
        production_exposure = _production_per_exposure_pct(row)
        if row['source_pnl_pct'] is not None:
            source_pnl[row['source_order_id']] = row['source_pnl_pct']
            if production:
                source_dollars.setdefault(row['source_order_id'], production)
            if production_exposure is not None:
                source_exposure.setdefault(row['source_order_id'], production_exposure)
        if row['status'] == 'skipped':
            entry['skipped'][row['skip_reason']] = entry['skipped'].get(row['skip_reason'], 0) + 1
        elif row['status'] == 'failed':
            entry['failed'] += 1
        elif row['status'] in ACTIVE:
            entry['active'] += 1
        elif row['exit_fill_price'] is not None and row['entry_fill_price']:
            pnl, capital = _trade_pnl(row)
            entry['closed'] += 1
            entry['wins'] += pnl > 0
            entry['pnl'] += pnl
            entry['capital'] += capital
            entry['pnl_pcts'].append(pnl / capital * 100)
            if row['source_pnl_pct'] is not None:
                entry['source_pcts'].append(row['source_pnl_pct'])
            if production:
                entry['source_pnl'] += production[0]
                entry['source_capital'] += production[1]
            own_exposure = _trade_per_exposure_pct(row, pnl)
            if own_exposure is not None:
                entry['exposure_pcts'].append(own_exposure)
            if production_exposure is not None:
                entry['source_exposure_pcts'].append(production_exposure)
    for entry in summary.values():
        entry['avg_pnl_pct'] = _mean(entry.pop('pnl_pcts'))
        entry['avg_pnl_per_exposure_pct'] = _mean(entry.pop('exposure_pcts'))
        entry['production_avg_pnl_per_exposure_pct'] = _mean(entry.pop('source_exposure_pcts'))
        entry['production_avg_pnl_pct'] = _mean(entry.pop('source_pcts'))
        entry['production_pnl_on_capital_pct'] = _on_capital_pct(entry.pop('source_pnl'), entry.pop('source_capital'))
    if source_pnl:
        # 'active' is mirrored entries production has not recorded a result for yet.
        summary[PRIMARY_NAME] = {'strategy': PRIMARY_STRATEGY, 'role': 'primary', 'closed': len(source_pnl),
                                 'active': len(source_open), 'wins': sum(pct > 0 for pct in source_pnl.values()),
                                 'avg_pnl_pct': _mean(list(source_pnl.values())),
                                 'avg_pnl_per_exposure_pct': _mean(list(source_exposure.values())),
                                 'pnl': sum(pnl for pnl, _ in source_dollars.values()),
                                 'capital': sum(capital for _, capital in source_dollars.values())}
    return summary


def _iso(value):
    """One timestamp format for the dashboard; stored values mix broker and SQLite styles."""
    return utc(value).isoformat() if value else None


def follower_report(days=0, *, now=None, entry_kind=COPY, db_path=None):
    """Closed trades per account for the dashboard, with production's result on the same entries.

    Production appears only on entries at least one follower closed, so its line covers the same
    trades. Its dollars are its realized return on what it actually paid (fill price x filled
    quantity), falling back to the estimated premium every follower was given as its budget.
    `days` keeps entries mirrored within the window; 0 is all.
    `entry_kind` is as in account_summary.
    """
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat() if days else None
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute('''SELECT p.*, h.pnl_pct AS source_pnl_pct, h.closed_at AS source_closed_at,
                e.fill_price * e.filled_qty * 100 AS source_cost, e.fill_price AS source_fill_price
            FROM account_positions p LEFT JOIN (
                SELECT trade_execution_order_id, AVG(realized_pnl_pct) AS pnl_pct, MAX(closed_at) AS closed_at
                FROM manager_decision_history WHERE closed_at IS NOT NULL AND realized_pnl_pct IS NOT NULL
                GROUP BY trade_execution_order_id) h ON h.trade_execution_order_id=p.source_order_id
            LEFT JOIN option_trade_executions e ON e.order_id=p.source_order_id
            WHERE (? IS NULL OR datetime(p.created_at) >= datetime(?)) AND p.entry_kind=? ORDER BY p.id''',
            (since, since, entry_kind)).fetchall()
    trades, production = [], {}
    for row in map(_decoded, rows):
        if row['status'] != 'closed' or row['exit_fill_price'] is None or not row['entry_fill_price']:
            continue
        common = {'underlying': row['underlying'], 'decision': row['decision'],
                  'source_order_id': row['source_order_id'], 'production_pnl_pct': row['source_pnl_pct']}
        pnl, capital = _trade_pnl(row)
        trades.append({**common, 'account': row['account'], 'strategy': row['strategy'], 'role': row['role'],
                       'opened_at': _iso(row['entry_at']), 'closed_at': _iso(row['exit_at']),
                       'exit_reason': row['exit_reason'], 'pnl': pnl, 'capital': capital,
                       'pnl_pct': pnl / capital * 100, 'pnl_per_exposure_pct': _trade_per_exposure_pct(row, pnl)})
        dollars = _production_dollars(row)
        if dollars and row['source_order_id'] not in production:
            production[row['source_order_id']] = {
                **common, 'account': row['source_account'], 'strategy': PRIMARY_STRATEGY, 'role': 'primary',
                'opened_at': None, 'closed_at': _iso(row['source_closed_at']), 'exit_reason': None,
                'pnl': dollars[0], 'capital': dollars[1], 'pnl_pct': row['source_pnl_pct'],
                'pnl_per_exposure_pct': _production_per_exposure_pct(row)}
    trades = sorted([*trades, *production.values()], key=lambda trade: trade['closed_at'])
    return {'as_of': now.isoformat(), 'days': days,
            'accounts': account_summary(since=since, entry_kind=entry_kind, db_path=db_path), 'trades': trades}
