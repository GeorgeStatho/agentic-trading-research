"""Inspect, scan or consume the shadow discovery queue without broker access."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for folder in ('Data', 'Python Scripts', 'Python Scripts/agentCallers'):
    sys.path.insert(0, str(ROOT / folder))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db')
    parser.add_argument('command', choices=('scan', 'consume', 'status'))
    args = parser.parse_args()
    from db_common import DB_PATH
    from market_db_modules.bootstrap import initialize_database
    db_path = args.db or DB_PATH
    initialize_database(db_path=db_path)
    if args.command == 'scan':
        from services.event_discovery import discover_company_events
        result = discover_company_events(db_path=db_path)
    elif args.command == 'consume':
        from services.discovery_queue import process_discovery_queue
        result = process_discovery_queue(db_path=db_path)
    else:
        from market_db_modules.discovery_queue import discovery_queue_status
        result = discovery_queue_status(db_path=db_path)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
