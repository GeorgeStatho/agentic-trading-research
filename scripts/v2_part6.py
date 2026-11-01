#!/usr/bin/env python3
"""Frozen research runs, counterfactual collection and cited reports. No broker calls."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db')
    commands = parser.add_subparsers(dest='command', required=True)
    start = commands.add_parser('start')
    start.add_argument('--kind', choices=('frozen_forward', 'leakage_checked_backtest'), default='frozen_forward')
    start.add_argument('--candidates', type=int, default=200)
    start.add_argument('--manifest', help='Saved pipeline manifest JSON; default is current effective configuration')
    collect = commands.add_parser('collect')
    collect.add_argument('run_id')
    collect.add_argument('--as-of')
    report = commands.add_parser('report')
    report.add_argument('run_id')
    report.add_argument('--month', required=True)
    report.add_argument('--as-of')
    gate = commands.add_parser('promote-gate')
    gate.add_argument('report_id')
    gate.add_argument('stage')
    champion = commands.add_parser('promote-champion')
    champion.add_argument('report_id')
    champion.add_argument('--incumbent-report', help='Incumbent re-evaluation on the challenger observation cohort')
    commands.add_parser('status')
    args = parser.parse_args()
    if args.db:
        os.environ['DB_PATH'] = str(Path(args.db).resolve())
    from market_db_modules.bootstrap import initialize_database
    from market_db_modules.research_runs import collect_run, start_run
    from market_db_modules.research_reports import monthly_report, promote_champion, promote_gate
    from db_common import get_connection
    initialize_database()
    if args.command == 'start':
        if args.manifest:
            manifest = json.loads(Path(args.manifest).read_text())
        else:
            from services.core.pipeline_version import build_pipeline_version
            manifest = build_pipeline_version()
        result = start_run(manifest, kind=args.kind, target_candidates=args.candidates)
    elif args.command == 'collect':
        result = collect_run(args.run_id, as_of=args.as_of)
    elif args.command == 'report':
        result = monthly_report(args.run_id, args.month, as_of=args.as_of)
    elif args.command == 'promote-gate':
        result = promote_gate(args.report_id, args.stage)
    elif args.command == 'promote-champion':
        result = promote_champion(args.report_id, incumbent_report_id=args.incumbent_report)
    else:
        with closing(get_connection()) as conn, conn:
            result = {'runs': [dict(row) for row in conn.execute('SELECT run_id,kind,started_at,status,target_candidates,config_hash FROM research_runs')],
                      'champion': [dict(row) for row in conn.execute('SELECT * FROM research_champion')],
                      'promotions': [dict(row) for row in conn.execute('SELECT * FROM research_gate_promotions')]}
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
