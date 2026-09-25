"""Seen-job history in the data directory.

Before M1B this database lived at app/storage/job_history.sqlite3. On first
use it is copied (read-only, never modified or deleted) into the data
directory; LEGACY_HISTORY_PATH controls the old location, and an empty value
turns adoption off.
"""
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import settings
from app.models.schemas import JobPosting
from app.services.job_identity import identity_keys
from app.storage import db


def default_db_path() -> Path:
    return Path(settings.data_dir) / "job_history.sqlite3"


DB_PATH = default_db_path()
LEGACY_DB_PATH: Path | None = Path(settings.legacy_history_path) if settings.legacy_history_path else None


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS seen_jobs (
        fingerprint TEXT PRIMARY KEY, company TEXT NOT NULL, title TEXT NOT NULL,
        location TEXT, application_url TEXT, first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("CREATE TABLE IF NOT EXISTS seen_job_identities "
                 "(identity TEXT PRIMARY KEY, first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP)")


MIGRATIONS = [
    db.Migration("history_v1", _create_tables,
                 "Restore data/backups/<time>/job_history.sqlite3, or delete data/job_history.sqlite3 "
                 "to re-adopt the untouched legacy file on next start."),
]


def _adopt_legacy() -> None:
    legacy = LEGACY_DB_PATH
    if DB_PATH.exists() or legacy is None or not legacy.exists() or legacy.resolve() == DB_PATH.resolve():
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    staging = DB_PATH.with_suffix(".adopting")
    # Read-only source: the legacy file is never written to.
    with closing(sqlite3.connect(legacy.resolve().as_uri() + "?mode=ro", uri=True)) as source, \
         closing(sqlite3.connect(staging)) as target:
        source.backup(target)
    staging.replace(DB_PATH)


def init_db():
    _adopt_legacy()
    db.ensure(DB_PATH, MIGRATIONS)


def fingerprint(job: JobPosting) -> str:
    domain = ""
    if job.application_url:
        domain = urlparse(str(job.application_url)).netloc.lower()

    raw = "|".join([
        job.company.strip().lower(),
        job.title.strip().lower(),
        job.location.strip().lower(),
        domain,
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_seen(job: JobPosting) -> bool:
    init_db()
    with db.connect(DB_PATH) as conn:
        # Read old fingerprints without rewriting/deleting legacy history.
        if conn.execute("SELECT 1 FROM seen_jobs WHERE fingerprint=?", (fingerprint(job),)).fetchone():
            return True
        return any(conn.execute("SELECT 1 FROM seen_job_identities WHERE identity=?", (key,)).fetchone()
                   for key in identity_keys(job))


def mark_seen(job: JobPosting):
    init_db()
    with db.connect(DB_PATH) as conn:
        conn.executemany("INSERT OR IGNORE INTO seen_job_identities(identity) VALUES (?)",
                         [(key,) for key in identity_keys(job)])
