"""Clear entity names stored as news article summaries by older scrapers."""
import argparse
from contextlib import closing

from db_common import get_connection

PLACEHOLDER_WHERE = "summary IN ('World News', 'U.S. News') OR summary IN (SELECT name FROM sectors)"


def clear_placeholder_summaries(*, apply=False, db_path=None):
    with closing(get_connection(db_path)) as conn, conn:
        counts = dict(conn.execute(
            f'SELECT summary, COUNT(*) FROM news_articles WHERE {PLACEHOLDER_WHERE} GROUP BY summary'
        ).fetchall())
        if apply:
            conn.execute(f'UPDATE news_articles SET summary = NULL WHERE {PLACEHOLDER_WHERE}')
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    counts = clear_placeholder_summaries(apply=args.apply, db_path=args.db or None)
    for summary, count in counts.items():
        print(f'{summary}: {count}')
    total = sum(counts.values())
    print(f'total: {total}')
    print(f'cleared {total} summaries' if args.apply else 'dry run; pass --apply to clear')


if __name__ == '__main__':
    main()
