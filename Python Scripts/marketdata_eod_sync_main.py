"""Keep the backtest quote cache updated without a browser or trading worker."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from threading import Event
import signal

from services.core.logging_utils import configure_logging
from services.core.settings import load_process_settings

if __name__ == "__main__":
    # Before importing modules that read configuration at import time.
    load_process_settings()

from market_db_modules.bootstrap import initialize_database
from market_db_modules.marketdata_sync import EodSyncSettings, run_due_eod_sync
from services.core.runtime_paths import DATA_DIR
from services.core.status_files import StatusReporter

CHECK_INTERVAL_SECONDS = 60


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Check the daily schedule once, then exit.")
    args = parser.parse_args()
    configure_logging()
    settings = EodSyncSettings.from_env()
    status_path = os.getenv("MARKETDATA_EOD_SYNC_STATUS_PATH", str(DATA_DIR / "marketdata_eod_sync_status.json"))
    status = StatusReporter(Path(status_path))
    initialize_database()
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    logging.info("EOD cache scheduler started: enabled=%s, daily at 10:00 America/New_York, batch_size=%s",
                 settings.enabled, settings.batch_size)
    last_idle_status = None
    while not stop.is_set():
        status.write("running", "Checking the EOD cache schedule")
        try:
            result = run_due_eod_sync(settings)
            if "fetch_run_id" in result or args.once:
                logging.info("EOD cache sync: %s", json.dumps(result))
            elif result["status"] != last_idle_status:
                logging.info("EOD cache sync: %s", result["status"])
            last_idle_status = result["status"]
            if result["status"] == "missing_token":
                status.write("error", "EOD cache sync is enabled but has no market-data token")
            else:
                status.write("paused", f"EOD cache sync: {result['status']}", sleep_seconds=CHECK_INTERVAL_SECONDS)
        except Exception as exc:
            logging.exception("EOD cache sync failed; daily claim and budget are preserved")
            status.write("error", f"EOD cache sync failed: {exc}")
            if args.once:
                raise
        if args.once:
            break
        stop.wait(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
