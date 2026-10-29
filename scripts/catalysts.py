"""Label catalysts the headline rules left in needs_review, and inspect the labels."""
import argparse
import json


def positive(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError('must be positive')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db')
    subparsers = parser.add_subparsers(dest='command', required=True)
    classify = subparsers.add_parser('classify', help='label pending catalysts, newest first (calls the model)')
    classify.add_argument('--limit', type=positive, default=None, help='default: CATALYST_CLASSIFIER_MAX_PER_CYCLE')
    subparsers.add_parser('status', help='counts by status, classification source, type and confidence (read-only)')
    review = subparsers.add_parser('review', help='random labelled catalysts with their headlines (read-only)')
    review.add_argument('--sample', type=positive, default=20)
    args = parser.parse_args()
    db_path = args.db or None
    if args.command == 'classify':
        from services.catalyst_classification import classify_pending_catalysts
        result = classify_pending_catalysts(limit=args.limit, db_path=db_path)
    else:
        from contextlib import closing
        from db_common import get_connection
        from market_db_modules.bootstrap import initialize_database
        from news_db_modules import catalyst_labels
        initialize_database(db_path=db_path)
        with closing(get_connection(db_path)) as conn:
            result = (catalyst_labels.catalyst_label_status(conn=conn) if args.command == 'status'
                      else {'sample': catalyst_labels.labeled_catalyst_sample(args.sample, conn=conn)})
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
