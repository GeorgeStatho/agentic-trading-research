"""Inspect, scan or consume the shadow discovery queue without broker access."""
import argparse
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db')
    parser.add_argument('command', choices=('scan', 'consume', 'status', 'checks', 'interest'))
    args = parser.parse_args()
    from market_db_modules.bootstrap import initialize_database
    db_path = args.db or None
    initialize_database(db_path=db_path)
    if args.command == 'scan':
        from services.discovery.event_discovery import discover_company_events
        result = discover_company_events(db_path=db_path)
    elif args.command == 'consume':
        from services.discovery.discovery_queue import process_discovery_queue
        result = process_discovery_queue(db_path=db_path)
    elif args.command in {'checks', 'interest'}:
        from contextlib import closing
        from datetime import datetime, timezone
        from db_common import get_connection
        at = datetime.now(timezone.utc).isoformat()
        with closing(get_connection(db_path)) as conn:
            if args.command == 'interest':
                rows = conn.execute('''SELECT c.symbol,i.* FROM company_interest i JOIN companies c ON c.id=i.company_id
                    WHERE julianday(i.selected_at)<=julianday(?) AND julianday(i.expires_at)>julianday(?)
                    ORDER BY c.symbol,i.source,i.source_key''', (at, at))
            else:
                rows = conn.execute('''SELECT c.symbol,d.* FROM discovery_checks d JOIN companies c ON c.id=d.company_id
                    ORDER BY d.next_check_at,c.symbol''')
            result = [dict(row) for row in rows]
    else:
        from market_db_modules.discovery_queue import discovery_queue_status
        result = discovery_queue_status(db_path=db_path)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
