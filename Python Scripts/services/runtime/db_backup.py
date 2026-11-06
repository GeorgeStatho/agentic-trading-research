"""The nightly full copy of the database that replaces the previous one, and backup health checks.

Point-in-time recovery is Litestream's job (config/litestream.yml); this module only watches that its
replica keeps up, so one status file covers both kinds of backup. See docs/BACKUPS.md.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timezone
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
from zoneinfo import ZoneInfo

from db_common import analyze_database, database_path
from services.core.runtime_paths import DATA_DIR

LOGGER = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo("America/New_York")
LATEST_NAME = "stock_experiment.latest.db"
MANIFEST_NAME = "stock_experiment.latest.json"


@dataclass(frozen=True)
class DbBackupSettings:
    db_path: Path
    backup_dir: Path
    temp_dir: Path
    replica_dir: Path | None
    backup_time: clock_time
    max_replica_lag_seconds: int
    max_backup_age_hours: int
    retry_seconds: int

    @classmethod
    def from_env(cls) -> DbBackupSettings:
        db_path = Path(database_path(None))
        backup_dir = Path(os.getenv("DB_BACKUP_DIR", str(DATA_DIR / "backups")))
        replica = os.getenv("DB_BACKUP_REPLICA_DIR", str(backup_dir / "litestream" / "stock_experiment"))
        hour, minute = (int(part) for part in os.getenv("DB_BACKUP_TIME", "05:00").split(":"))
        return cls(
            db_path=db_path,
            backup_dir=backup_dir,
            # SQLite writes to the 9p/drvfs backup drive are slow and unreliable, so build the copy here.
            temp_dir=Path(os.getenv("DB_BACKUP_TEMP_DIR", str(db_path.parent / ".backup-tmp"))),
            replica_dir=Path(replica) if replica else None,
            backup_time=clock_time(hour, minute),
            max_replica_lag_seconds=int(os.getenv("DB_BACKUP_MAX_REPLICA_LAG_SECONDS", "600")),
            max_backup_age_hours=int(os.getenv("DB_BACKUP_MAX_AGE_HOURS", "26")),
            retry_seconds=int(os.getenv("DB_BACKUP_RETRY_SECONDS", "3600")),
        )


def read_manifest(backup_dir: Path) -> dict:
    try:
        return json.loads((backup_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def due_schedule_date(now: datetime, backup_time: clock_time, manifest: dict) -> date | None:
    """Today's market date once its backup time has passed and that day has no backup yet."""
    local = now.astimezone(MARKET_TZ)
    if local.time() < backup_time or manifest.get("schedule_date") == local.date().isoformat():
        return None
    return local.date()


def backup_database(settings: DbBackupSettings, *, schedule_date: date, now=lambda: datetime.now(timezone.utc)) -> dict:
    """Write a checked copy of the database over the previous one; the previous one survives any failure."""
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    local_copy = settings.temp_dir / "stock_experiment.backup.db"
    remote_temp = settings.backup_dir / f"{LATEST_NAME}.tmp"
    started_at = now()
    try:
        local_copy.unlink(missing_ok=True)
        # VACUUM INTO is one read transaction: a consistent copy while writers continue under WAL.
        with closing(sqlite3.connect(settings.db_path, timeout=60)) as source:
            source.execute("VACUUM INTO ?", (str(local_copy),))
        with closing(sqlite3.connect(f"file:{local_copy}?mode=ro", uri=True)) as copy:
            check = [row[0] for row in copy.execute("PRAGMA quick_check")]
            schema = sha256("\n".join(sql for (sql,) in copy.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name")).encode()).hexdigest()
        if check != ["ok"]:
            raise RuntimeError(f"Backup copy failed quick_check: {'; '.join(check[:3])}")
        shutil.copyfile(local_copy, remote_temp)
        os.replace(remote_temp, settings.backup_dir / LATEST_NAME)
    finally:
        local_copy.unlink(missing_ok=True)
        remote_temp.unlink(missing_ok=True)
    manifest = {
        "schedule_date": schedule_date.isoformat(),
        "started_at": started_at.isoformat(),
        "completed_at": now().isoformat(),
        "source_bytes": settings.db_path.stat().st_size,
        "backup_bytes": (settings.backup_dir / LATEST_NAME).stat().st_size,
        "quick_check": "ok",
        "schema_sha256": schema,
    }
    manifest_temp = settings.backup_dir / f"{MANIFEST_NAME}.tmp"
    manifest_temp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(manifest_temp, settings.backup_dir / MANIFEST_NAME)
    try:
        analyze_database(settings.db_path)
    except sqlite3.Error as exc:
        # Statistics are an optimisation; a busy database must not turn a good backup into a failure.
        LOGGER.warning("Planner statistics were not refreshed: %s", exc)
    return manifest


def replica_lag_seconds(db_path: Path, replica_dir: Path) -> float | None:
    """How far the newest Litestream replica file trails the last database write; None without a replica."""
    generations = replica_dir / "generations"
    if not generations.is_dir():
        return None
    current = max(generations.iterdir(), key=lambda path: path.stat().st_mtime, default=None)
    if current is None:
        return None
    newest = max((entry.stat().st_mtime for folder in (current / "wal", current / "snapshots") if folder.is_dir()
                  for entry in os.scandir(folder) if entry.is_file()), default=None)
    if newest is None:
        return None
    written = max(path.stat().st_mtime for path in (db_path, Path(f"{db_path}-wal")) if path.exists())
    return max(0.0, written - newest)


def backup_problem(settings: DbBackupSettings, manifest: dict, now: datetime) -> str | None:
    """Why the backups cannot be trusted right now, or None."""
    completed = manifest.get("completed_at")
    if not completed:
        return "No nightly backup has completed yet"
    age_hours = (now - datetime.fromisoformat(completed)).total_seconds() / 3600
    if age_hours > settings.max_backup_age_hours:
        return f"The nightly backup is {age_hours:.0f} hours old"
    if settings.replica_dir is None:
        return None
    lag = replica_lag_seconds(settings.db_path, settings.replica_dir)
    if lag is None:
        return "The point-in-time replica has not started"
    if lag > settings.max_replica_lag_seconds:
        return f"The point-in-time replica is {lag / 60:.0f} minutes behind"
    return None
