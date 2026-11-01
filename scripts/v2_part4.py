#!/usr/bin/env python3
"""Collect catalyst price responses and report pricing coverage; no broker orders."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', help='Database path; defaults to DB_PATH')
    commands = parser.add_subparsers(dest='command', required=True)
    collect = commands.add_parser('collect', help='Register all classified catalysts and capture completed outcomes')
    collect.add_argument('--cached-only', action='store_true', help='Use existing bars without network requests')
    collect.add_argument('--symbol-budget', type=int, help='Maximum symbols fetched this run (two calls per symbol)')
    commands.add_parser('report', help='Coverage and forward returns by absorption bucket')
    stats = commands.add_parser('stats', help='Mature family/horizon distribution as of now')
    stats.add_argument('family')
    stats.add_argument('--horizon-days', type=float, default=5)
    args = parser.parse_args()
    from export_alpaca_trade_history import load_env_file
    load_env_file()
    if args.db:
        os.environ['DB_PATH'] = str(Path(args.db).resolve())
    if getattr(args, 'symbol_budget', None) is not None:
        os.environ['V2_RESEARCH_SYMBOL_BUDGET'] = str(args.symbol_budget)
    from market_db_modules.bootstrap import initialize_database
    from market_db_modules.pricing_research import pricing_report, catalyst_stats
    from services.gates.pricing_policy import PricingPolicy
    initialize_database()
    if args.command == 'collect':
        from services.discovery.catalyst_response_collector import collect_catalyst_responses
        result = collect_catalyst_responses(refresh=not args.cached_only)
    elif args.command == 'stats':
        result = catalyst_stats(args.family, args.horizon_days, as_of=datetime.now(timezone.utc),
                                minimum_samples=PricingPolicy.from_env().minimum_samples)
    else:
        result = pricing_report()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
