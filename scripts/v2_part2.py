#!/usr/bin/env python3
"""Inspect V2 telemetry or refresh it using read-only broker/provider calls."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', help='Database to inspect/migrate; defaults to DB_PATH')
    commands = parser.add_subparsers(dest='command', required=True)
    execution = commands.add_parser('execution', help='Fill coverage, rates and slippage by account mode and side')
    execution.add_argument('--start', help='Inclusive submission date YYYY-MM-DD')
    execution.add_argument('--end', help='Exclusive submission date YYYY-MM-DD')
    funnel = commands.add_parser('funnel', help='Stages and rejection metrics for one UTC candidate cohort')
    funnel.add_argument('date')
    lifecycle = commands.add_parser('lifecycle', help='Verify snapshot hashes and inspect a candidate without fetching data')
    lifecycle.add_argument('candidate_id')
    commands.add_parser('exits', help='Exit attribution and observation coverage')
    commands.add_parser('post-exit', help='Refresh due daily post-exit and benchmark returns')
    commands.add_parser('fills', help='Read broker orders and persist fills; submits no orders')
    args = parser.parse_args()
    from export_alpaca_trade_history import load_env_file
    load_env_file()
    if args.db:
        os.environ['DB_PATH'] = str(Path(args.db).resolve())
    logging.basicConfig(level=logging.INFO)
    from market_db import (initialize_database, execution_telemetry_report, candidate_funnel,
                                   load_candidate_lifecycle, exit_attribution_report)
    initialize_database()
    if args.command == 'execution':
        result = execution_telemetry_report(start=args.start, end=args.end)
    elif args.command == 'funnel':
        result = candidate_funnel(date=args.date)
    elif args.command == 'lifecycle':
        result = load_candidate_lifecycle(args.candidate_id)
    elif args.command == 'exits':
        result = exit_attribution_report()
    elif args.command == 'post-exit':
        from services.position_telemetry import refresh_post_exit_returns
        result = refresh_post_exit_returns()
    else:
        from services.execution_telemetry import reconcile_execution_telemetry
        from services.position_telemetry import ensure_tracked_positions
        from services.trading_gateway import AlpacaTradingGateway
        gateway = AlpacaTradingGateway(paper=os.getenv('ALPACA_PAPER', 'true').lower() in {'true', '1', 'yes'},
                                       logger=logging.getLogger(__name__))
        result = reconcile_execution_telemetry(gateway=gateway, trading_client=gateway.create_client())
        ensure_tracked_positions()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
