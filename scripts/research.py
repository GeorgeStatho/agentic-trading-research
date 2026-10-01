"""Consume shadow research, replay recorded sessions or inspect trade economics, without provider access."""
import argparse
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db')
    subparsers = parser.add_subparsers(dest='command', required=True)
    consume = subparsers.add_parser('consume')
    consume.add_argument('--limit', type=int, default=6)
    replay = subparsers.add_parser('replay')
    replay.add_argument('session_id')
    theses = subparsers.add_parser('theses', help='write shadow evidence theses for finished research sessions')
    theses.add_argument('--limit', type=int, default=6)
    thesis = subparsers.add_parser('thesis', help='show one recorded evidence thesis check')
    thesis.add_argument('evaluation_id')
    report = subparsers.add_parser('thesis-report', help='summarise thesis checks and gate verdicts (read-only)')
    report.add_argument('--days', type=int, default=7, help='look-back window in days; 0 for all history')
    report.add_argument('--route', choices=('legacy_strategist', 'research_shadow'))
    economics = subparsers.add_parser('economics', help='show one recorded trade-economics report')
    economics.add_argument('report_id')
    economics_report = subparsers.add_parser('economics-report',
                                             help='summarise trade-economics reports and rejections (read-only)')
    economics_report.add_argument('--days', type=int, default=7, help='look-back window in days; 0 for all history')
    args = parser.parse_args()
    from db_common import DB_PATH
    db_path = args.db or DB_PATH
    if args.command == 'replay':
        from market_db_modules.research_runtime import replay_research_session
        result = replay_research_session(args.session_id, db_path=db_path)
    elif args.command == 'thesis':
        from market_db_modules.evidence_thesis import get_evidence_thesis
        result = get_evidence_thesis(args.evaluation_id, db_path=db_path)
    elif args.command == 'thesis-report':
        if args.days < 0:
            parser.error('--days must be 0 or positive')
        from market_db_modules.evidence_thesis_report import evidence_thesis_report
        result = evidence_thesis_report(days=args.days, route=args.route, db_path=db_path)
    elif args.command == 'economics':
        from market_db_modules.trade_economics import get_candidate_economics_report
        result = get_candidate_economics_report(args.report_id, db_path=db_path)
    elif args.command == 'economics-report':
        if args.days < 0:
            parser.error('--days must be 0 or positive')
        from market_db_modules.trade_economics import trade_economics_summary
        result = trade_economics_summary(days=args.days, db_path=db_path)
    elif args.command == 'theses':
        if not 1 <= args.limit <= 100:
            parser.error('--limit must be between 1 and 100')
        from services.research_thesis import process_research_theses
        result = process_research_theses(limit=args.limit, db_path=db_path)
    else:
        if not 1 <= args.limit <= 100:
            parser.error('--limit must be between 1 and 100')
        from services.research_orchestrator import process_research_queue
        result = process_research_queue(limit=args.limit, db_path=db_path)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
