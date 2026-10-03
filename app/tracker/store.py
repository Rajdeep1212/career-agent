"""The tracker's database: where it lives, how it is opened, and its Alembic migrations.

Local and tests: `data/tracker.sqlite3`, a file of its own, so `data/agent.sqlite3` and the radar index are never
touched by the tracker. Deployment (not decided yet): a Postgres URL through the same functions.
"""
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import settings

DB_PATH = Path(settings.data_dir) / "tracker.sqlite3"
LOCAL_USER_ID = "local"                 # the one user of AUTH_MODE=local; real users come with TRK4
LOCAL_USER_EMAIL = "local@localhost"
_MIGRATIONS = Path(__file__).resolve().parent / "migrations"
_ENGINES: dict[str, Engine] = {}


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


def upgrade(url: str | None = None, revision: str = "head") -> None:
    if (url or default_url()).startswith("sqlite"):
        Path((url or default_url()).removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(_config(url), revision)


def downgrade(url: str | None = None, revision: str = "base") -> None:
    command.downgrade(_config(url), revision)
