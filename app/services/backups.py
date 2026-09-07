"""Database backups.

Backups use SQLite's online backup API rather than a file copy, so a snapshot
taken while the app is running is always internally consistent — a plain
``shutil.copy`` of a WAL database can capture a torn state.
"""

from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
from pathlib import Path

from app import config, db, logs

PREFIX = "re4-"
SUFFIX = ".db"


class BackupError(Exception):
    """Raised for user-facing backup and restore failures."""


def backups_dir() -> Path:
    config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    return config.BACKUPS_DIR


def create(label: str = "") -> Path:
    """Snapshot the live database. Returns the path written."""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = f"-{_safe(label)}" if label else ""
    # Two snapshots in the same second must not share a filename: the second
    # would silently overwrite the first, which is exactly the kind of loss a
    # backup exists to prevent. A restore writes its safety copy moments after
    # a startup backup, so this happens in practice, not in theory.
    counter = 1
    while True:
        target = backups_dir() / (
            f"{PREFIX}{stamp}{tag}{SUFFIX}" if counter == 1
            else f"{PREFIX}{stamp}-{counter}{tag}{SUFFIX}"
        )
        if not target.exists():
            break
        counter += 1

    source = db.get_connection()
    try:
        destination = sqlite3.connect(str(target))
        try:
            source.backup(destination)
        finally:
            destination.close()
    except sqlite3.Error as exc:
        target.unlink(missing_ok=True)
        raise BackupError(f"Could not write the backup: {exc}") from exc

    logs.info("Backup written to %s (%s bytes)", target, target.stat().st_size)
    return target


def list_backups() -> list[dict]:
    """Newest first, with size and timestamp ready for display."""
    entries = []
    for path in sorted(backups_dir().glob(f"{PREFIX}*{SUFFIX}"), reverse=True):
        try:
            stat = path.stat()
        except OSError:
            # Leaving it out of the list is right - it cannot be restored from
            # - but doing so without a word would have the shop believe it has
            # one backup fewer than it does.
            logs.warning("Could not read the backup %s; leaving it off the list", path)
            continue
        entries.append({
            "path": path,
            "name": path.name,
            "size_kb": round(stat.st_size / 1024, 1),
            "taken_at": dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return entries


def prune(keep: int) -> int:
    """Delete the oldest backups beyond ``keep``. Returns how many were removed."""
    if keep <= 0:
        return 0
    removed = 0
    for entry in list_backups()[keep:]:
        try:
            entry["path"].unlink()
            removed += 1
        except OSError:
            logs.warning("Could not delete old backup %s", entry["path"])
    return removed


def run_startup_backup() -> Path | None:
    """Called once at launch when the setting is on. Never fatal."""
    from app.services import settings as settings_service

    try:
        if settings_service.get("backup_on_start", "1") != "1":
            return None
        path = create("startup")
        prune(int(settings_service.get("backup_keep", "20") or 20))
        return path
    except Exception:  # noqa: BLE001 - a failed backup must not stop the shop opening
        logs.exception("Startup backup failed")
        return None


def run_shutdown_backup() -> Path | None:
    """Called once at closing when the setting is on. Never fatal."""
    from app.services import settings as settings_service

    try:
        if settings_service.get("backup_on_close", "0") != "1":
            return None
        path = create("shutdown")
        prune(int(settings_service.get("backup_keep", "20") or 20))
        return path
    except Exception:  # noqa: BLE001 - a failed backup must not hold the door locked
        logs.exception("Shutdown backup failed")
        return None


def restore(backup_path) -> Path:
    """Replace the live database with a backup.

    The current database is snapshotted first, so an accidental restore of the
    wrong file is itself reversible. Returns the safety copy's path.
    """
    backup_path = Path(backup_path)
    if not backup_path.exists():
        raise BackupError(f"Backup not found: {backup_path}")

    _verify_restorable(backup_path)
    safety = create("before-restore")

    live = db.database_path()
    db.close_connection()
    try:
        # WAL sidecars belong to the old database; leaving them would corrupt
        # the restored file.
        for sidecar in (live.with_name(live.name + "-wal"), live.with_name(live.name + "-shm")):
            sidecar.unlink(missing_ok=True)
        shutil.copyfile(backup_path, live)
    except OSError as exc:
        raise BackupError(f"Could not restore the backup: {exc}") from exc
    finally:
        db.get_connection()  # reopen against the file now in place

    db.init_db()
    logs.info("Restored %s over %s (safety copy at %s)", backup_path, live, safety)
    return safety


def _verify_restorable(path: Path) -> None:
    """Refuse anything that is not a healthy RE4 database."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise BackupError(f"{path.name} is not a readable SQLite database.") from exc
    try:
        result = conn.execute("PRAGMA quick_check").fetchone()
        if result is None or str(result[0]).lower() != "ok":
            raise BackupError(f"{path.name} failed its integrity check and was not restored.")
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = {"users", "products", "sales"} - tables
        if missing:
            raise BackupError(
                f"{path.name} does not look like an {config.APP_NAME} database "
                f"(missing: {', '.join(sorted(missing))})."
            )
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > db.SCHEMA_VERSION:
            raise BackupError(
                f"{path.name} was written by a newer version of {config.APP_NAME}."
            )
    except sqlite3.Error as exc:
        raise BackupError(f"{path.name} could not be read: {exc}") from exc
    finally:
        conn.close()


def _safe(text: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "-"
                   for character in text).strip("-")
