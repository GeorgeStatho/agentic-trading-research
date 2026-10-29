#!/usr/bin/env python3
"""Follower accounts next to production on the same entries. Reads the database only; no broker calls."""
import argparse
import json
import sqlite3


def _pct(value):
    return '-' if value is None else f'{value:+.1f}%'


def _rows(summary):
    production = summary.get('production', {})
    yield ('account', 'strategy', 'role', 'closed', 'active', 'failed', 'skipped', 'win %', 'P&L $',
           'P&L / capital', 'avg trade', 'production avg', 'delta')
    if production:
        yield ('production', production['strategy'], 'primary', production['closed'], '-', '-', '-', '-', '-', '-',
               _pct(production['avg_pnl_pct']), _pct(production['avg_pnl_pct']), '-')
    for name, entry in summary.items():
        if name == 'production':
            continue
        closed, average, source = entry['closed'], entry['avg_pnl_pct'], entry['production_avg_pnl_pct']
        skipped = ', '.join(f'{reason}={count}' for reason, count in sorted(entry['skipped'].items())) or '0'
        yield (name, entry['strategy'], entry['role'], closed, entry['active'], entry['failed'], skipped,
               f'{100 * entry["wins"] / closed:.0f}%' if closed else '-', f'{entry["pnl"]:+,.2f}',
               _pct(100 * entry['pnl'] / entry['capital']) if entry['capital'] else '-',
               _pct(average), _pct(source),
               _pct(average - source) if average is not None and source is not None else '-')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db')
    parser.add_argument('--since', help='Only positions created at or after this ISO time')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    from market_db_modules.account_positions import account_summary

    # No initialize_database: a report must never run schema setup or migrations on the target DB.
    try:
        summary = account_summary(since=args.since, db_path=args.db)
    except sqlite3.OperationalError as exc:
        print(f'No follower data in this database ({exc}).')
        return
    if args.json:
        print(json.dumps(summary, indent=2))
        return
    if not summary:
        print('No follower positions recorded yet.')
        return
    rows = [tuple(map(str, row)) for row in _rows(summary)]
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    for row in rows:
        print('  '.join(cell.ljust(width) for cell, width in zip(row, widths)))
    print('\nPartial production exits are not mirrored: followers close fully when production fully closes.')


if __name__ == '__main__':
    main()
