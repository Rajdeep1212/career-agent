"""Jobs from alert emails and the bookmarklet (data/alerts.sqlite3).

Each job is keyed by its platform id (e.g. linkedin:4012345678) or, for saved
pages, its canonical URL. The same job from two platforms (same company, title
and city) is stored once, with the other listing added to its sources[].
Processed emails are remembered by Message-ID so re-runs skip them.
"""
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.models.schemas import JobPosting, JobSourceRef
from app.services.job_identity import city_key, company_key, title_key
from app.storage import db

DB_PATH = Path(settings.data_dir) / "alerts.sqlite3"


def _create_v1(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS alert_jobs (
        key TEXT PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL, identity_key TEXT NOT NULL,
        job_json TEXT NOT NULL, radar_key TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
        times_seen INTEGER NOT NULL DEFAULT 1)""")
    conn.execute("CREATE INDEX IF NOT EXISTS alert_jobs_identity ON alert_jobs(identity_key)")
    conn.execute("""CREATE TABLE IF NOT EXISTS alert_messages (
        message_key TEXT PRIMARY KEY, origin TEXT NOT NULL, platform TEXT, jobs_found INTEGER NOT NULL,
        processed_at TEXT NOT NULL)""")


MIGRATIONS = [db.Migration("alerts_v1", _create_v1,
                           "Restore data/backups/<time>/alerts.sqlite3, or delete data/alerts.sqlite3 and re-drop the .eml files.")]


@dataclass
class UpsertResult:
    new: int = 0
    duplicates: int = 0   # already stored: seen again, or the same job from another platform


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    db.ensure(DB_PATH, MIGRATIONS)
    return db.connect(DB_PATH)


def identity_key(job: JobPosting) -> str:
    """Company, title and city folded the same way as Radar identity resolution."""
    return "|".join((company_key(job.company), title_key(job.title), city_key(job.location) or ""))


def message_processed(message_key: str) -> bool:
    if not DB_PATH.exists():
        return False
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM alert_messages WHERE message_key=?", (message_key,)).fetchone() is not None


def record_message(message_key: str, *, origin: str, platform: str | None, jobs_found: int) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO alert_messages VALUES (?, ?, ?, ?, ?)",
                     (message_key, origin, platform, jobs_found, _now()))


def upsert(jobs: list[JobPosting], *, kind: str) -> UpsertResult:
    """Store new jobs; a job seen again, or listed by another platform, updates the existing record."""
    result = UpsertResult()
    now = _now()
    with _connect() as conn:
        for job in jobs:
            key = str(job.source_job_id or job.application_url)
            existing = conn.execute("SELECT key, job_json FROM alert_jobs WHERE key=?", (key,)).fetchone()
            if existing is None:
                existing = conn.execute("SELECT key, job_json FROM alert_jobs WHERE identity_key=? ORDER BY first_seen_at LIMIT 1",
                                        (identity_key(job),)).fetchone()
            if existing is None:
                conn.execute("INSERT INTO alert_jobs (key, kind, source, identity_key, job_json, first_seen_at, last_seen_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (key, kind, job.source or kind, identity_key(job), job.model_dump_json(), now, now))
                result.new += 1
                continue
            stored = JobPosting.model_validate_json(existing["job_json"])
            if existing["key"] != key:
                ref = JobSourceRef(source=job.source or kind, url=str(job.application_url) if job.application_url else None,
                                   source_job_id=job.source_job_id)
                if ref not in stored.sources:
                    stored.sources.append(ref)
            conn.execute("UPDATE alert_jobs SET job_json=?, last_seen_at=?, times_seen=times_seen+1 WHERE key=?",
                         (stored.model_dump_json(), now, existing["key"]))
            result.duplicates += 1
    return result


def set_radar_key(job: JobPosting, radar_key: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE alert_jobs SET radar_key=? WHERE key=? OR identity_key=?",
                     (radar_key, str(job.source_job_id or job.application_url), identity_key(job)))


def has_jobs() -> bool:
    """Whether any alert or saved job is stored; never creates the database."""
    if not DB_PATH.exists():
        return False
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM alert_jobs LIMIT 1").fetchone() is not None


def list_jobs() -> list[JobPosting]:
    if not DB_PATH.exists():
        return []
    with _connect() as conn:
        rows = conn.execute("SELECT job_json FROM alert_jobs ORDER BY last_seen_at DESC").fetchall()
    return [JobPosting.model_validate_json(row["job_json"]) for row in rows]


def rows() -> list[dict]:
    if not DB_PATH.exists():
        return []
    with _connect() as conn:
        return [dict(row) for row in conn.execute("SELECT key, kind, source, radar_key, times_seen FROM alert_jobs").fetchall()]
