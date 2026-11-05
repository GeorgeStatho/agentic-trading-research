"""Consume shadow research, replay sessions, and inspect economics, event reviews, forward evaluation and benchmarks."""
import argparse
import json


def run_benchmark_command(parser, args, db_path):
    command = args.benchmark_command
    if command == 'create':
        if args.size < 1 or not 0 <= args.holdout < 1:
            parser.error('--size must be positive and --holdout in [0, 1)')
        from services.evaluation.benchmark_selection import create_benchmark_set
        return create_benchmark_set(args.set_id, size=args.size, holdout_share=args.holdout, start_at=args.start_at,
                                    end_at=args.end_at, db_path=db_path)
    if command == 'show':
        from market_db_modules.benchmarks import benchmark_set_summary
        return benchmark_set_summary(args.set_id, db_path=db_path)
    if command == 'run':
        if args.split == 'holdout' and not args.confirm_holdout:
            parser.error('the holdout can be scored only once per model; pass --confirm-holdout to spend it')
        from services.evaluation.benchmark_runner import run_benchmark
        return run_benchmark(args.set_id, split=args.split, model=args.model, replay_strategist=args.replay_strategist,
                             allow_unknown_cutoff=args.allow_unknown_cutoff, db_path=db_path)
    from services.evaluation.benchmark_runner import compare_benchmark
    return compare_benchmark(args.set_id, a=args.a, b=args.b, split=args.split, db_path=db_path)


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
    attribution = subparsers.add_parser('attribution',
                                        help='evidence quality vs direction outcome of manager decisions (read-only)')
    attribution.add_argument('--days', type=int, default=0, help='look-back window in days; 0 for all history')
    attribution.add_argument('--horizon', type=int, choices=(5, 10), default=5, help='trading days the outcome is graded at')
    economics = subparsers.add_parser('economics', help='show one recorded trade-economics report')
    economics.add_argument('report_id')
    economics_report = subparsers.add_parser('economics-report',
                                             help='summarise trade-economics reports and rejections (read-only)')
    economics_report.add_argument('--days', type=int, default=7, help='look-back window in days; 0 for all history')
    event_review = subparsers.add_parser('event-review', help='show one recorded event-triggered thesis review')
    event_review.add_argument('review_id')
    event_reviews = subparsers.add_parser('event-reviews', help='summarise shadow event-triggered thesis reviews (read-only)')
    event_reviews.add_argument('--days', type=int, default=7, help='look-back window in days; 0 for all history')
    subparsers.add_parser('forward-status', help='forward cohorts, paired evaluations and closing marks (read-only)')
    compare = subparsers.add_parser('compare', help='write a paired incumbent/challenger report for a cohort')
    compare.add_argument('cohort_id')
    compare.add_argument('--as-of')
    comparison = subparsers.add_parser('comparison', help='show one recorded comparison report')
    comparison.add_argument('report_id')
    promote = subparsers.add_parser('promote-challenger',
                                    help='record approval of a final comparison that met its pre-registered criteria')
    promote.add_argument('report_id')
    ledger = subparsers.add_parser('discovery-ledger', help='route status and outcomes of admitted events (read-only)')
    ledger.add_argument('--days', type=int, default=30)
    ledger.add_argument('--events', action='store_true', help='include the per-event ledger')
    outcomes = subparsers.add_parser('research-outcomes', help='research-route thesis outcomes, cost and latency (read-only)')
    outcomes.add_argument('--days', type=int, default=30, help='look-back window in days; 0 for all history')
    subparsers.add_parser('forward-cycle', help="run the collector's forward-evaluation step once")
    news_lag = subparsers.add_parser('news-lag',
                                     help='news-to-order lag buckets, pre/post-entry underlying move and option P&L (read-only)')
    news_lag.add_argument('--days', type=int, default=0, help='look-back window in days; 0 for all history')
    news_lag.add_argument('--fetch-missing', action='store_true',
                          help='first cache daily and ~2 years of hourly bars for traded underlyings (network)')
    benchmark = subparsers.add_parser('benchmark', help='frozen benchmark sets for screening challengers (never promotes)')
    benchmark_commands = benchmark.add_subparsers(dest='benchmark_command', required=True)
    create = benchmark_commands.add_parser('create', help='select and store a new benchmark set')
    create.add_argument('--set-id', required=True)
    create.add_argument('--size', type=int, default=300)
    create.add_argument('--holdout', type=float, default=0.3, help='share of items held out, by symbol')
    create.add_argument('--start-at')
    create.add_argument('--end-at')
    benchmark_commands.add_parser('show', help='strata counts and the split summary').add_argument('set_id')
    run = benchmark_commands.add_parser('run', help='replay and score one split with a model')
    run.add_argument('set_id')
    run.add_argument('--model', required=True, help="a model id, or 'stored' for production's own answers")
    run.add_argument('--split', choices=('dev', 'holdout'), default='dev')
    run.add_argument('--replay-strategist', action='store_true')
    run.add_argument('--allow-unknown-cutoff', action='store_true')
    run.add_argument('--confirm-holdout', action='store_true', help='required for --split holdout, which can be scored only once')
    versus = benchmark_commands.add_parser('compare', help='paired comparison of two runs on a split')
    versus.add_argument('set_id')
    versus.add_argument('--a', required=True, help='MODEL or MODEL:PROMPTS (manager prompt version)')
    versus.add_argument('--b', required=True, help='MODEL or MODEL:PROMPTS (manager prompt version)')
    versus.add_argument('--split', choices=('dev', 'holdout'), default='dev')
    args = parser.parse_args()
    db_path = args.db or None
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
    elif args.command == 'attribution':
        if args.days < 0:
            parser.error('--days must be 0 or positive')
        from market_db_modules.decision_attribution_report import decision_attribution_report
        result = decision_attribution_report(days=args.days, horizon=args.horizon, db_path=db_path)
    elif args.command == 'economics':
        from market_db_modules.trade_economics import get_candidate_economics_report
        result = get_candidate_economics_report(args.report_id, db_path=db_path)
    elif args.command == 'economics-report':
        if args.days < 0:
            parser.error('--days must be 0 or positive')
        from market_db_modules.trade_economics import trade_economics_summary
        result = trade_economics_summary(days=args.days, db_path=db_path)
    elif args.command == 'event-review':
        from market_db_modules.thesis_event_reviews import find_thesis_event_review
        result = find_thesis_event_review(args.review_id, db_path=db_path)
    elif args.command == 'event-reviews':
        if args.days < 0:
            parser.error('--days must be 0 or positive')
        from market_db_modules.thesis_event_reviews import thesis_event_review_summary
        result = thesis_event_review_summary(days=args.days, db_path=db_path)
    elif args.command == 'forward-status':
        from market_db_modules.forward_evaluation import forward_status
        from services.research.research_marks import mark_status
        result = {**forward_status(db_path=db_path), 'marks': mark_status(db_path=db_path)}
    elif args.command == 'compare':
        from market_db_modules.forward_comparison import comparison_report
        result = comparison_report(args.cohort_id, as_of=args.as_of, db_path=db_path)
    elif args.command == 'comparison':
        from market_db_modules.forward_comparison import get_comparison_report
        result = get_comparison_report(args.report_id, db_path=db_path)
    elif args.command == 'promote-challenger':
        from market_db_modules.forward_comparison import promote_challenger
        result = promote_challenger(args.report_id, db_path=db_path)
    elif args.command == 'discovery-ledger':
        if args.days < 1:
            parser.error('--days must be positive')
        from market_db_modules.discovery_evaluation import discovery_route_ledger
        result = discovery_route_ledger(days=args.days, db_path=db_path)
        if not args.events:
            result.pop('admitted_events')
    elif args.command == 'research-outcomes':
        if args.days < 0:
            parser.error('--days must be 0 or positive')
        from market_db_modules.research_outcomes import research_route_report
        result = research_route_report(days=args.days, db_path=db_path)
    elif args.command == 'news-lag':
        if args.days < 0:
            parser.error('--days must be 0 or positive')
        from market_db_modules.news_lag_report import fetch_missing_history, news_lag_report
        fetched = fetch_missing_history(days=args.days, db_path=db_path) if args.fetch_missing else None
        result = news_lag_report(days=args.days, db_path=db_path)
        if fetched is not None:
            result['fetched_history'] = fetched
    elif args.command == 'benchmark':
        result = run_benchmark_command(parser, args, db_path)
    elif args.command == 'forward-cycle':
        from services.evaluation.forward_evaluation import run_forward_evaluation_cycle
        result = run_forward_evaluation_cycle(db_path=db_path)
    elif args.command == 'theses':
        if not 1 <= args.limit <= 100:
            parser.error('--limit must be between 1 and 100')
        from services.research.research_thesis import process_research_theses
        result = process_research_theses(limit=args.limit, db_path=db_path)
    else:
        if not 1 <= args.limit <= 100:
            parser.error('--limit must be between 1 and 100')
        from services.research.research_orchestrator import process_research_queue
        result = process_research_queue(limit=args.limit, db_path=db_path)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
