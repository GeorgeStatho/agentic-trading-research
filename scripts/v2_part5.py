#!/usr/bin/env python3
"""Part 5 coverage and execution baseline reports. Never submits orders."""
import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('report')
    baseline = commands.add_parser('baseline')
    baseline.add_argument('--live', action='store_true')
    baseline.add_argument('--regime')
    args = parser.parse_args()
    from export_alpaca_trade_history import load_env_file
    load_env_file()
    if args.db:
        os.environ['DB_PATH'] = str(Path(args.db).resolve())
    from market_db_modules.bootstrap import initialize_database
    from market_db_modules.contract_research import contract_report, execution_baseline
    initialize_database()
    result = execution_baseline(paper=not args.live, regime_state=args.regime) if args.command == 'baseline' else contract_report()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
