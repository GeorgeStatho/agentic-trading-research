"""Keep the backtest quote cache updated without a browser or trading worker."""
from __future__ import annotations

import argparse
import json
import logging
from threading import Event
import signal

from services.core.settings import load_process_settings

if __name__ == "__main__":
    # Before importing modules that read configuration at import time.
    load_process_settings()

from market_db_modules.bootstrap import initialize_database
from market_db_modules.marketdata_sync import EodSyncSettings, run_due_eod_sync


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Check the daily schedule once, then exit.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = EodSyncSettings.from_env()
    initialize_database()
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    logging.info("EOD cache scheduler started: enabled=%s, daily at 10:00 America/New_York, batch_size=%s",
                 settings.enabled, settings.batch_size)
    last_idle_status = None
    while not stop.is_set():
        try:
            result = run_due_eod_sync(settings)
            if "fetch_run_id" in result or args.once:
                logging.info("EOD cache sync: %s", json.dumps(result))
            elif result["status"] != last_idle_status:
                logging.info("EOD cache sync: %s", result["status"])
            last_idle_status = result["status"]
        except Exception:
            logging.exception("EOD cache sync failed; daily claim and budget are preserved")
            if args.once:
                raise
        if args.once:
            break
        stop.wait(60)


if __name__ == "__main__":
    main()
