"""Positions held by trading accounts. In v1 only follower accounts write rows."""
from contextlib import closing
from dataclasses import asdict
import json

from db_common import get_connection
from services.account_strategies import Skip
from services.trading_accounts import PRIMARY_NAME, PRIMARY_STRATEGY

ACTIVE = ('pending_entry', 'open', 'closing')


def _update(row_id, db_path, sql, *values):
    with closing(get_connection(db_path)) as conn, conn:
        conn.execute(f'UPDATE account_positions SET {sql} WHERE id=?', (*values, row_id))


def record_plan(account, *, source_account, source_order_id, source_option_symbol, underlying, decision,
                budget, plan, db_path=None):
    """Record one account's response to a source entry; None when it was already recorded."""
    skipped = isinstance(plan, Skip)
    values = {
        'account': account.name, 'role': account.role, 'strategy': account.strategy,
        'exit_policy': account.exit_policy, 'source_account': source_account,
        'source_order_id': source_order_id, 'source_option_symbol': source_option_symbol,
        'underlying': underlying, 'decision': decision, 'budget': budget,
        'status': 'skipped' if skipped else 'pending_entry',
        'skip_reason': plan.reason if skipped else None,
        'legs_json': '[]' if skipped else json.dumps([asdict(leg) for leg in plan.legs]),
        'qty': None if skipped else plan.qty, 'multiplier': None if skipped else plan.multiplier,
        'unit_cost': None if skipped else plan.unit_cost,
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
    scale = position['qty'] * position['multiplier']
    capital = position['entry_fill_price'] * scale
    return sign * (position['exit_fill_price'] - position['entry_fill_price']) * scale, capital


def _mean(values):
    return sum(values) / len(values) if values else None


def account_summary(*, since=None, db_path=None):
    """Per-account results, plus the primary's own result on the entries the followers mirrored."""
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute('''SELECT p.*, (SELECT AVG(h.realized_pnl_pct) FROM manager_decision_history h
                WHERE h.trade_execution_order_id=p.source_order_id) AS source_pnl_pct
            FROM account_positions p WHERE ? IS NULL OR datetime(p.created_at) >= datetime(?) ORDER BY p.id''',
                            (since, since)).fetchall()
    summary, source_pnl = {}, {}
    for row in map(_decoded, rows):
        entry = summary.setdefault(row['account'], {
            'strategy': row['strategy'], 'role': row['role'], 'closed': 0, 'active': 0, 'failed': 0,
            'skipped': {}, 'wins': 0, 'pnl': 0.0, 'capital': 0.0, 'pnl_pcts': [], 'source_pcts': []})
        if row['source_pnl_pct'] is not None:
            source_pnl[row['source_order_id']] = row['source_pnl_pct']
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
    for entry in summary.values():
        entry['avg_pnl_pct'] = _mean(entry.pop('pnl_pcts'))
        entry['production_avg_pnl_pct'] = _mean(entry.pop('source_pcts'))
    if source_pnl:
        summary[PRIMARY_NAME] = {'strategy': PRIMARY_STRATEGY, 'role': 'primary', 'closed': len(source_pnl),
                                 'avg_pnl_pct': _mean(list(source_pnl.values()))}
    return summary
