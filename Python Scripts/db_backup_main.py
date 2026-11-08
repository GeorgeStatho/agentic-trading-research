"""Replace the full database backup every night and report whether the backups can be trusted."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import signal
from threading import Event

from services.core.logging_utils import configure_logging
from services.core.settings import load_process_settings

if __name__ == "__main__":
    # Before importing modules that read configuration at import time.
    load_process_settings()

from services.core.runtime_paths import DATA_DIR
from services.core.status_files import StatusReporter
from services.runtime.db_backup import (MARKET_TZ, DbBackupSettings, backup_database, backup_problem,
                                        due_schedule_date, read_manifest)

CHECK_INTERVAL_SECONDS = 60


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Back up now, whatever the schedule, then exit.")
    args = parser.parse_args()
    configure_logging()
    settings = DbBackupSettings.from_env()
    status_path = os.getenv("DB_BACKUP_STATUS_PATH", str(DATA_DIR / "db_backup_status.json"))
    status = StatusReporter(Path(status_path))
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    logging.info("Database backup started: %s -> %s daily at %s America/New_York",
                 settings.db_path, settings.backup_dir, settings.backup_time.strftime("%H:%M"))
    retry_at = last_problem = None
    while not stop.is_set():
        now = datetime.now(timezone.utc)
        manifest = read_manifest(settings.backup_dir)
        schedule_date = (now.astimezone(MARKET_TZ).date() if args.once
                         else due_schedule_date(now, settings.backup_time, manifest))
        if schedule_date and (args.once or retry_at is None or now >= retry_at):
            status.write("running", "Backing up the database")
            try:
                manifest = backup_database(settings, schedule_date=schedule_date)
                retry_at = None
                logging.info("Database backup written: %s bytes", manifest["backup_bytes"])
            except Exception as exc:
                # A failure leaves the previous backup in place; retry later rather than every minute.
                retry_at = now + timedelta(seconds=settings.retry_seconds)
                logging.exception("Database backup failed; the previous backup is kept")
                status.write("error", f"Database backup failed: {exc}")
                if args.once:
                    raise
        problem = backup_problem(settings, manifest, datetime.now(timezone.utc))
        if problem != last_problem and problem:
            logging.warning(problem)
        last_problem = problem
        if problem:
            status.write("error", problem)
        else:
            status.write("paused", f"Last backup {manifest['completed_at']}", sleep_seconds=CHECK_INTERVAL_SECONDS,
                         last_backup_at=manifest["completed_at"])
        if args.once:
            break
        stop.wait(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
