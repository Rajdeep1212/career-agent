"""Company Radar index: official listings, their open/closed status and sync runs.

Status comes from the company's own board:
- listed in today's fetch            -> ACTIVE_VERIFIED
- missing from a complete source on
  two different days                 -> CLOSED
- missing from an incomplete source
  (a capped sitemap window)          -> unchanged; the sync checks the job page
"""
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.models.schemas import JobPosting
from app.storage import db

DB_PATH = Path(settings.data_dir) / "radar.sqlite3"
CLOSE_AFTER_MISSING_DAYS = 2


def _create_v1(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE radar_jobs (
        key TEXT PRIMARY KEY,                    -- company_id:source_job_id
        company_id TEXT NOT NULL,
        source_job_id TEXT NOT NULL,
        job_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'CLOSED')),
        status_reason TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,              -- last time the source listed it
        last_listed_on TEXT NOT NULL,            -- day of that listing
        last_checked_on TEXT NOT NULL,           -- day of the latest sync that considered it
        missing_days INTEGER NOT NULL DEFAULT 0,
        last_missing_on TEXT,
        closed_at TEXT)""")
    conn.execute("CREATE INDEX radar_jobs_company ON radar_jobs(company_id, status)")
    conn.execute("""CREATE TABLE radar_job_sources (
        radar_key TEXT NOT NULL REFERENCES radar_jobs(key),
        source TEXT NOT NULL,
        external_id TEXT NOT NULL,
        url TEXT,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        PRIMARY KEY (radar_key, source, external_id))""")
    conn.execute("""CREATE TABLE radar_sync_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id TEXT NOT NULL,
        day TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL CHECK (status IN ('running', 'ok', 'partial', 'error', 'skipped')),
        fetched_count INTEGER,                   -- len() of the list the source returned
        india_count INTEGER,                     -- len() after the India filter
        listed_count INTEGER,
        new_count INTEGER,
        closed_count INTEGER,
        http_status INTEGER,
        error TEXT)""")
    conn.execute("CREATE INDEX radar_sync_runs_day ON radar_sync_runs(company_id, day)")


def _create_v2(conn: sqlite3.Connection) -> None:
    # Job pages already read and found outside India or expired, so a sitemap sync does not re-open them daily.
    conn.execute("""CREATE TABLE radar_rejected (
        company_id TEXT NOT NULL,
        source_job_id TEXT NOT NULL,
        checked_on TEXT NOT NULL,
        PRIMARY KEY (company_id, source_job_id))""")


_ROLLBACK = "Restore data/backups/<time>/radar.sqlite3, or delete data/radar.sqlite3; the next sync rebuilds it."
MIGRATIONS = [db.Migration("radar_v1", _create_v1, _ROLLBACK), db.Migration("radar_v2", _create_v2, _ROLLBACK)]


@dataclass
class ListingResult:
    listed: int
    new: int
    closed: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    db.ensure(DB_PATH, MIGRATIONS)
    return db.connect(DB_PATH)


def _key(company_id: str, source_job_id: str) -> str:
    return f"{company_id}:{source_job_id}"


def _job_from_row(row) -> JobPosting:
    job = JobPosting.model_validate_json(row["job_json"])
    active = row["status"] == "ACTIVE"
    return job.model_copy(update={
        "verification_state": "ACTIVE_VERIFIED" if active else "CLOSED",
        "application_status": "active" if active else "closed",
        "verification_reason": row["status_reason"],
        "verification_checked_at": row["last_checked_on"],
        "official_application": True,
    })


def record_listing(company_id: str, jobs: list[JobPosting], *, complete: bool, today: date) -> ListingResult:
    """Apply one sync's listing for a company; jobs must carry source_job_id."""
    day, now = today.isoformat(), _now()
    listed_keys, new = set(), 0
    with _connect() as conn:
        for job in jobs:
            key = _key(company_id, str(job.source_job_id))
            listed_keys.add(key)
            reason = f"Listed on the company's official board on {day}."
            existing = conn.execute("SELECT key FROM radar_jobs WHERE key=?", (key,)).fetchone()
            if existing is None:
                new += 1
                conn.execute("""INSERT INTO radar_jobs (key, company_id, source_job_id, job_json, status, status_reason,
                                first_seen_at, last_seen_at, last_listed_on, last_checked_on) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?)""",
                             (key, company_id, str(job.source_job_id), job.model_dump_json(), reason, now, now, day, day))
            else:
                conn.execute("""UPDATE radar_jobs SET job_json=?, status='ACTIVE', status_reason=?, last_seen_at=?,
                                last_listed_on=?, last_checked_on=?, missing_days=0, last_missing_on=NULL, closed_at=NULL WHERE key=?""",
                             (job.model_dump_json(), reason, now, day, day, key))
        closed = 0
        if not complete:
            # A capped window: absence proves nothing; remember the check so the sync can verify job pages.
            conn.execute("UPDATE radar_jobs SET last_checked_on=? WHERE company_id=? AND status='ACTIVE'", (day, company_id))
        if complete:
            rows = conn.execute("SELECT key, missing_days, last_missing_on FROM radar_jobs WHERE company_id=? AND status='ACTIVE'",
                                (company_id,)).fetchall()
            for row in rows:
                if row["key"] in listed_keys or row["last_missing_on"] == day:
                    continue
                missing = row["missing_days"] + 1
                if missing >= CLOSE_AFTER_MISSING_DAYS:
                    closed += 1
                    conn.execute("""UPDATE radar_jobs SET status='CLOSED', missing_days=?, last_missing_on=?, last_checked_on=?,
                                    closed_at=?, status_reason=? WHERE key=?""",
                                 (missing, day, day, now, f"No longer listed on the company's official board (checked {day}).", row["key"]))
                else:
                    conn.execute("UPDATE radar_jobs SET missing_days=?, last_missing_on=?, last_checked_on=? WHERE key=?",
                                 (missing, day, day, row["key"]))
    return ListingResult(listed=len(listed_keys), new=new, closed=closed)


def missing_from_window(company_id: str) -> list[JobPosting]:
    """Active jobs of an incomplete (capped) source that its latest fetch did not list."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM radar_jobs WHERE company_id=? AND status='ACTIVE' AND last_listed_on < last_checked_on",
                            (company_id,)).fetchall()
    return [_job_from_row(row) for row in rows]


def known_jobs(company_id: str) -> dict[str, JobPosting]:
    """Active jobs of a company as stored, keyed by source id."""
    with _connect() as conn:
        rows = conn.execute("SELECT source_job_id, job_json FROM radar_jobs WHERE company_id=? AND status='ACTIVE'",
                            (company_id,)).fetchall()
    return {row["source_job_id"]: JobPosting.model_validate_json(row["job_json"]) for row in rows}


def remember_rejected(company_id: str, source_job_ids: list[str], *, today: date) -> None:
    with _connect() as conn:
        conn.executemany("INSERT OR REPLACE INTO radar_rejected (company_id, source_job_id, checked_on) VALUES (?, ?, ?)",
                         [(company_id, job_id, today.isoformat()) for job_id in source_job_ids])


def rejected_ids(company_id: str) -> set[str]:
    with _connect() as conn:
        rows = conn.execute("SELECT source_job_id FROM radar_rejected WHERE company_id=?", (company_id,)).fetchall()
    return {row["source_job_id"] for row in rows}


def mark_closed(company_id: str, source_job_id: str, reason: str, *, today: date) -> None:
    with _connect() as conn:
        conn.execute("UPDATE radar_jobs SET status='CLOSED', status_reason=?, closed_at=?, last_checked_on=? WHERE key=?",
                     (reason, _now(), today.isoformat(), _key(company_id, source_job_id)))


def list_jobs(*, include_closed: bool = False, company_id: str | None = None) -> list[JobPosting]:
    query = "SELECT * FROM radar_jobs WHERE 1=1"
    params: list = []
    if not include_closed:
        query += " AND status='ACTIVE'"
    if company_id:
        query += " AND company_id=?"
        params.append(company_id)
    with _connect() as conn:
        return [_job_from_row(row) for row in conn.execute(query + " ORDER BY first_seen_at DESC", params).fetchall()]


def new_since(since: str) -> list[JobPosting]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM radar_jobs WHERE status='ACTIVE' AND first_seen_at >= ? ORDER BY first_seen_at DESC",
                            (since,)).fetchall()
    return [_job_from_row(row) for row in rows]


def start_run(company_id: str, *, today: date) -> int:
    with _connect() as conn:
        cursor = conn.execute("INSERT INTO radar_sync_runs (company_id, day, started_at, status) VALUES (?, ?, ?, 'running')",
                              (company_id, today.isoformat(), _now()))
        return int(cursor.lastrowid or 0)


def finish_run(run_id: int, *, status: str, fetched: int | None = None, india: int | None = None, listed: int | None = None,
               new: int | None = None, closed: int | None = None, http_status: int | None = None, error: str | None = None) -> None:
    with _connect() as conn:
        conn.execute("""UPDATE radar_sync_runs SET finished_at=?, status=?, fetched_count=?, india_count=?, listed_count=?,
                        new_count=?, closed_count=?, http_status=?, error=? WHERE id=?""",
                     (_now(), status, fetched, india, listed, new, closed, http_status, error, run_id))


def ran_today(company_id: str, *, today: date) -> bool:
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM radar_sync_runs WHERE company_id=? AND day=? AND status IN ('ok', 'partial')",
                            (company_id, today.isoformat())).fetchone() is not None


def runs(*, day: date | None = None) -> list[dict]:
    with _connect() as conn:
        if day:
            rows = conn.execute("SELECT * FROM radar_sync_runs WHERE day=? ORDER BY id", (day.isoformat(),)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM radar_sync_runs ORDER BY id DESC LIMIT 500").fetchall()
    return [dict(row) for row in rows]


def last_sync() -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT MAX(finished_at) AS finished_at, COUNT(DISTINCT company_id) AS companies FROM radar_sync_runs "
                           "WHERE status IN ('ok', 'partial') AND day=(SELECT MAX(day) FROM radar_sync_runs)").fetchone()
        active = conn.execute("SELECT COUNT(*) FROM radar_jobs WHERE status='ACTIVE'").fetchone()[0]
    return {"finished_at": row["finished_at"], "companies": row["companies"], "active_jobs": active} if row and row["finished_at"] else None
