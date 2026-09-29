"""Collect requested company evidence or replay a frozen package offline."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for folder in ('Data', 'Python Scripts', 'Python Scripts/agentCallers'):
    sys.path.insert(0, str(ROOT / folder))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=None)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('collect')
    replay = commands.add_parser('replay')
    replay.add_argument('package_id')
    replay.add_argument('--as-of', help='Default: the original package cutoff')
    args = parser.parse_args()
    from db_common import DB_PATH
    db_path = args.db or DB_PATH
    if args.command == 'collect':
        from services.company_evidence_service import collect_company_evidence
        result = collect_company_evidence(db_path=db_path)
    else:
        from market_db_modules.company_evidence import get_evidence_package
        from agent_builders.company_evidence_payload import build_company_evidence_payload
        package = get_evidence_package(args.package_id, db_path=db_path)
        result = build_company_evidence_payload(package['company_id'], package_id=args.package_id,
                                                as_of=args.as_of or package['information_cutoff_time'], db_path=db_path)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
