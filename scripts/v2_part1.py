#!/usr/bin/env python3
"""V2 blocker maintenance. Broker operations in this command are read-only."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Database to migrate/backfill; defaults to DB_PATH")
    commands = parser.add_subparsers(dest="command", required=True)
    outcomes = commands.add_parser("outcomes", help="Reconcile broker-confirmed closed trades")
    outcomes.add_argument("--apply", action="store_true", help="Persist confirmed outcomes; otherwise preview")
    commands.add_parser("provenance", help="Backfill provenance and print source-count distribution")
    commands.add_parser("regime", help="Refresh SPY/VIX history and macro calendar")
    gates = commands.add_parser("gates", help="Backfill stored verdicts and report their realized outcomes")
    gates.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    from export_alpaca_trade_history import load_env_file
    load_env_file()
    if args.db:
        os.environ["DB_PATH"] = str(Path(args.db).resolve())
    logging.basicConfig(level=logging.INFO)
    if args.command == "outcomes":
        from market_db_modules.history_records import list_unresolved_manager_decisions
        from services.outcome_reconciliation import reconcile_closed_outcomes
        from services.config import AlpacaSettings
        from services.trading_gateway import AlpacaTradingGateway
        if not list_unresolved_manager_decisions():
            result = {"examined": 0, "resolved": 0, "ready": [], "unresolved": []}
        else:
            gateway = AlpacaTradingGateway(
                paper=AlpacaSettings.from_env().paper,
                logger=logging.getLogger(__name__),
            )
            result = reconcile_closed_outcomes(
                gateway=gateway, trading_client=gateway.create_client(), apply=args.apply,
            )
    elif args.command == "provenance":
        from news_db_modules.provenance import backfill_news_provenance
        result = backfill_news_provenance()
    elif args.command == "regime":
        from services.market_regime import build_market_regime_snapshot
        result = build_market_regime_snapshot()
    else:
        from market_db_modules.shadow_gates import backfill_gate_verdicts, list_shadow_gate_outcomes
        result = {"verdicts_backfilled": backfill_gate_verdicts(),
                  "outcomes": list_shadow_gate_outcomes(limit=args.limit)}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
