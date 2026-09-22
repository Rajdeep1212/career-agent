import hashlib
from contextlib import closing
from app.services.job_identity import identity_keys
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from app.models.schemas import JobPosting

DB_PATH = Path(__file__).resolve().parent / "job_history.sqlite3"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_jobs (
                fingerprint TEXT PRIMARY KEY,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                location TEXT,
                application_url TEXT,
                first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE TABLE IF NOT EXISTS seen_job_identities (identity TEXT PRIMARY KEY, first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.commit()


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
    with closing(sqlite3.connect(DB_PATH)) as conn:
        # Read old fingerprints without rewriting/deleting legacy history.
        if conn.execute("SELECT 1 FROM seen_jobs WHERE fingerprint=?", (fingerprint(job),)).fetchone():
            return True
        return any(conn.execute("SELECT 1 FROM seen_job_identities WHERE identity=?", (key,)).fetchone()
                   for key in identity_keys(job))


def mark_seen(job: JobPosting):
    init_db()
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.executemany("INSERT OR IGNORE INTO seen_job_identities(identity) VALUES (?)",
                         [(key,) for key in identity_keys(job)])
