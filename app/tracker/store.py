"""The tracker's database: where it lives, how it is opened, and its Alembic migrations.

Local and tests: `data/tracker.sqlite3`, a file of its own, so `data/agent.sqlite3` and the radar index are never
touched by the tracker. Deployment (not decided yet): a Postgres URL through the same functions.
"""
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.tracker import backup

DB_PATH = Path(settings.data_dir) / "tracker.sqlite3"
LOCAL_USER_ID = "local"                 # the one user of AUTH_MODE=local; real users come with TRK4
LOCAL_USER_EMAIL = "local@localhost"
_MIGRATIONS = Path(__file__).resolve().parent / "migrations"
_ENGINES: dict[str, Engine] = {}
_READY: set[str] = set()


def sqlite_url(path: Path) -> str:
    return "sqlite:///" + Path(path).resolve().as_posix()


def default_url() -> str:
    return sqlite_url(DB_PATH)


def engine(url: str | None = None) -> Engine:
    url = url or default_url()
    if url not in _ENGINES:
        if url.startswith("sqlite"):
            # No pooled connection keeps the file open (Windows would lock it), and foreign keys are enforced.
            created = create_engine(url, poolclass=NullPool)
            event.listen(created, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        else:
            created = create_engine(url)
        _ENGINES[url] = created
    return _ENGINES[url]


@contextmanager
def session(url: str | None = None) -> Iterator[Session]:
    with Session(engine(url)) as opened:
        yield opened


def dispose_all() -> None:
    for created in _ENGINES.values():
        created.dispose()
    _ENGINES.clear()


def _config(url: str | None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS))
    config.set_main_option("sqlalchemy.url", (url or default_url()).replace("%", "%%"))
    return config


def _backup_before_migration(path: Path, config: Config, revision: str) -> None:
    """Back up an existing database that has a migration pending; if no backup is made, nothing is applied."""
    if not path.exists():
        return
    with closing(sqlite3.connect(path)) as conn:
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.OperationalError:                         # no migration has run yet, so there is nothing to keep
            return
    target = ScriptDirectory.from_config(config).get_current_head() if revision == "head" else revision
    if row is None or row[0] == target:
        return
    report = backup.backup(path)
    if not report["backup"]:
        raise RuntimeError(f"Tracker migration not applied, because no backup was made. {report['message']}")


def upgrade(url: str | None = None, revision: str = "head") -> None:
    if (url or default_url()).startswith("sqlite"):
        path = Path((url or default_url()).removeprefix("sqlite:///"))
        path.parent.mkdir(parents=True, exist_ok=True)
        _backup_before_migration(path, _config(url), revision)
    command.upgrade(_config(url), revision)


def ensure(url: str | None = None) -> None:
    """Create or upgrade the database once per process per URL (again if a local file was removed)."""
    url = url or default_url()
    if url in _READY and (not url.startswith("sqlite") or Path(url.removeprefix("sqlite:///")).exists()):
        return
    upgrade(url)
    _READY.add(url)


def downgrade(url: str | None = None, revision: str = "base") -> None:
    command.downgrade(_config(url), revision)
