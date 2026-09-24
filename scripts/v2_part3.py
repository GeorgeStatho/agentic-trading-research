#!/usr/bin/env python3
"""V2 catalyst maintenance and research reports; no broker operations."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', help='Database to migrate/inspect; defaults to DB_PATH')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('backfill', help='Rebuild article provenance, catalyst states and opportunist impact metadata')
    commands.add_parser('families', help='Counts by coarse modelling family')
    freshness = commands.add_parser('freshness', help='Shadow outcome coverage by catalyst-age quartile')
    freshness.add_argument('--start', help='Inclusive UTC candidate date YYYY-MM-DD')
    freshness.add_argument('--end', help='Exclusive UTC candidate date YYYY-MM-DD')
    args = parser.parse_args()
    from export_alpaca_trade_history import load_env_file
    load_env_file()
    if args.db:
        os.environ['DB_PATH'] = str(Path(args.db).resolve())
    for folder in ('Data', 'Python Scripts', 'Python Scripts/agentCallers'):
        sys.path.insert(0, str(ROOT/folder))
    from market_db import initialize_database, freshness_outcome_report
    from news_db import initialize_database as initialize_news, backfill_catalysts, catalyst_family_report
    initialize_database()
    initialize_news()
    if args.command == 'backfill':
        result = backfill_catalysts()
    elif args.command == 'families':
        result = catalyst_family_report()
    else:
        result = freshness_outcome_report(start=args.start, end=args.end)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
