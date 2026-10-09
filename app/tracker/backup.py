"""Backups of the tracker database (docs/ROADMAP_QUEUE.md BAK1).

A backup is a whole, checked copy made with SQLite's online backup, so it is safe while the app is writing. Copies go to
`CAREER_AGENT_BACKUP_DIR`, or `backups/tracker/` beside the database, and the newest seven are kept. Only the tracker is
copied: `data/agent.sqlite3` holds sign-in tokens and is never backed up here.
"""
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

KEEP = 7
MIN_FREE_BYTES = 2 * 1024**3
_COUNTED = ("applications", "application_events")


def folder(db_path: Path) -> Path:
    chosen = os.environ.get("CAREER_AGENT_BACKUP_DIR", "").strip()
    return Path(chosen) if chosen else Path(db_path).parent / "backups" / "tracker"


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def _integrity(conn: sqlite3.Connection) -> str:
    return str(conn.execute("PRAGMA integrity_check").fetchone()[0])


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in _COUNTED}


def backup(db_path: Path, target: Path | None = None) -> dict:
    """Copy the database into the backup folder. `backup` is None when nothing was copied; `message` says why."""
    db_path = Path(db_path)
    target = Path(target or folder(db_path))
    if not db_path.exists():
        return {"backup": None, "message": f"No tracker database at {db_path}; nothing to back up."}
    target.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(target).free
    if free < MIN_FREE_BYTES:
        return {"backup": None, "message": f"Skipped: {target} has {free / 1024**3:.1f} GB free, below the 2 GB minimum. "
                                           "No backup was made."}
    while True:                                                 # Windows can give two calls the same clock reading
        final = target / f"tracker-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.sqlite3"
        if not final.exists():
            break
    partial = final.with_name(final.name + ".partial")          # never counted as a backup until it has passed the check
    try:
        with closing(_read_only(db_path)) as source, closing(sqlite3.connect(partial)) as copy:
            source.backup(copy)
            result = _integrity(copy)
        if result != "ok":
            raise RuntimeError(f"The backup copy failed its integrity check ({result}); it was removed.")
        partial.replace(final)
    finally:
        partial.unlink(missing_ok=True)
    for old in sorted(target.glob("tracker-*.sqlite3"))[:-KEEP]:
        old.unlink()
    return {"backup": str(final), "message": f"Backed up the tracker to {final} (integrity check ok)."}


def restore_check(db_path: Path, target: Path | None = None) -> dict:
    """Restore the newest backup into a temporary folder and compare its counts with the live file, which is only read."""
    db_path = Path(db_path)
    if not db_path.exists():
        return {"ok": False, "message": f"No tracker database at {db_path}; nothing to compare a backup with."}
    copies = sorted(Path(target or folder(db_path)).glob("tracker-*.sqlite3"))
    if not copies:
        return {"ok": False, "message": "No backup found. Run: python scripts/tracker_admin.py backup"}
    with tempfile.TemporaryDirectory() as directory:
        restored = Path(shutil.copy2(copies[-1], Path(directory) / db_path.name))
        with closing(sqlite3.connect(restored)) as conn:
            integrity, restored_counts = _integrity(conn), _counts(conn)
    with closing(_read_only(db_path)) as conn:
        live_counts = _counts(conn)
    ok = integrity == "ok" and restored_counts == live_counts
    message = ("The newest backup restores cleanly and matches the live tracker." if ok else
               "The newest backup does not match the live tracker. If the tracker changed since that backup, take a new one "
               "and check again.")
    return {"ok": ok, "backup": str(copies[-1]), "integrity": integrity, "restored": restored_counts, "live": live_counts,
            "message": message}
