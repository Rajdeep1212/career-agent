"""One SQLite helper for local stores: WAL connections, numbered migrations and a read-only path.

Migration rules (see docs/AUDIT_AND_ROADMAP.md §5.0):
- Existing data is backed up to ``<db dir>/backups/<UTC time>/`` before any
  pending migration runs; if the backup fails, nothing is applied.
- Each migration runs in its own transaction and is recorded in
  ``schema_migrations``, so re-running is a no-op.
- Every migration carries a rollback note (restore the backup, or a manual step).
"""
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()
_MIGRATED: set[Path] = set()
_READ_ONLY: set[Path] = set()


@dataclass(frozen=True)
class Migration:
    version: str
    apply: Callable[[sqlite3.Connection], None]
    rollback: str

    def __post_init__(self):
        if not self.version or not self.rollback.strip():
            raise ValueError("A migration needs a version and a rollback note")


class ReadOnlyError(RuntimeError):
    """A read-only database would have needed a write (a pending migration)."""


def is_read_only(path: Path) -> bool:
    return Path(path).resolve() in _READ_ONLY


@contextmanager
def read_only(path: Path) -> Iterator[None]:
    """Inside the block, every connection to `path` is read-only: nothing is created or written.

    For frozen copies (scripts/freeze_index.py), which must stay byte-identical to their manifest.
    """
    resolved = Path(path).resolve()
    added = resolved not in _READ_ONLY
    _READ_ONLY.add(resolved)
    try:
        yield
    finally:
        if added:
            _READ_ONLY.discard(resolved)


def _open_read_only(path: Path) -> sqlite3.Connection:
    # mode=ro never creates the file, a journal or a -wal/-shm pair; no journal_mode pragma is sent,
    # because switching to WAL is itself a write.
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _open(path: Path) -> sqlite3.Connection:
    if is_read_only(path):
        return _open_read_only(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
    except BaseException:
        conn.close()   # a file that cannot be written must not stay locked by a half-open connection
        raise
    return conn


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    """A WAL connection whose block commits on success and rolls back on error."""
    with closing(_open(Path(path))) as conn:
        conn.execute("BEGIN")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


def _backup(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.parent / "backups" / stamp / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    # The SQLite backup API copies a consistent snapshot, including WAL content.
    with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(target)) as copy:
        source.backup(copy)
    return target


def _has_user_tables(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name != 'schema_migrations' "
                        "AND name NOT LIKE 'sqlite_%' LIMIT 1").fetchone() is not None


def _check_migrated(path: Path, migrations: list[Migration]) -> None:
    """A read-only database is never migrated or backed up: it must already be up to date."""
    with _LOCK, closing(_open_read_only(path)) as conn:
        recorded = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")} if recorded else set()
    pending = [migration.version for migration in migrations if migration.version not in done]
    if pending:
        raise ReadOnlyError(f"{path} is read-only and has pending migrations: {', '.join(pending)}")


def migrate(path: Path, migrations: list[Migration]) -> list[str]:
    """Apply pending migrations in order; return the versions applied."""
    path = Path(path)
    if is_read_only(path):
        _check_migrated(path, migrations)
        return []
    with _LOCK, closing(_open(path)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        pending = [migration for migration in migrations if migration.version not in done]
        if not pending:
            return []
        if _has_user_tables(conn):
            _backup(path)
        applied = []
        for migration in pending:
            conn.execute("BEGIN IMMEDIATE")
            try:
                migration.apply(conn)
                conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                             (migration.version, datetime.now(timezone.utc).isoformat()))
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            applied.append(migration.version)
        return applied


def ensure(path: Path, migrations: list[Migration]) -> None:
    """Migrate once per process per database file (again if the file was removed)."""
    path = Path(path).resolve()
    if path in _MIGRATED and path.exists():
        return
    migrate(path, migrations)
    _MIGRATED.add(path)


def reset_cache() -> None:
    """For tests: forget which databases were migrated in this process."""
    _MIGRATED.clear()
