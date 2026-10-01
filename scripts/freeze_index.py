"""Freeze a read-only copy of the Company Radar index for relevance labelling (docs/M2_PLAN.md §5).

Usage (from the repository root, in the app's Python environment):
    python scripts/freeze_index.py

Copies data/radar.sqlite3 with the SQLite backup API (a consistent copy that includes WAL content)
into data/eval/frozen/<UTC time>/, writes manifest.json beside it, and marks both read-only. The
source index is only read. An existing folder is never overwritten.

To delete a frozen copy on Windows, clear the read-only attribute first.
"""
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.storage import radar_store  # noqa: E402

MANIFEST_NAME = "manifest.json"


def default_source() -> Path:
    return radar_store.DB_PATH


def default_root() -> Path:
    return Path(settings.data_dir) / "eval" / "frozen"


def _git_sha() -> str | None:
    try:
        done = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    # Each count is read from the copy itself, never estimated.
    return {table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables}


def freeze(source: Path | None = None, root: Path | None = None, *, stamp: str | None = None) -> Path:
    """Freeze `source` into `root/<stamp>/` and return that folder."""
    if settings.demo_mode:
        raise RuntimeError("The index is not frozen in demo mode: it holds no real index.")
    source = Path(source) if source else default_source()
    root = Path(root) if root else default_root()
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not source.is_file():
        raise FileNotFoundError(f"Index not found: {source}")
    folder = root / stamp
    if folder.exists():
        raise FileExistsError(f"A frozen copy already exists at {folder}; it is never overwritten.")
    folder.mkdir(parents=True)
    copy = folder / source.name
    with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as origin, \
            closing(sqlite3.connect(copy)) as target:
        origin.backup(target)
        # A rollback-journal database is one self-contained file: it can be opened read-only with no
        # -wal or -shm beside it.
        target.execute("PRAGMA journal_mode=DELETE")
        counts = _row_counts(target)
    manifest = {
        "file": copy.name,
        "source_path": str(source.resolve()),
        "frozen_at_utc": stamp,
        "sha256": hashlib.sha256(copy.read_bytes()).hexdigest(),
        "row_counts": counts,
        "git_sha": _git_sha(),
    }
    manifest_path = folder / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for path in (copy, manifest_path):
        os.chmod(path, stat.S_IREAD)
    return folder


if __name__ == "__main__":
    frozen = freeze()
    details = json.loads((frozen / MANIFEST_NAME).read_text(encoding="utf-8"))
    print(f"Frozen copy: {frozen / details['file']}")
    print(f"sha256: {details['sha256']}")
    print(f"row counts: {details['row_counts']}")
