"""The append-only job event log, job snapshots and CV versions (docs/M2_PLAN.md §1).

Events are keyed by job, so a thumbs label on a job that was never saved shares the log with the
tracker. `career_applications.status` is a cache of the latest funnel event that is not undone.
Rows in job_events, job_snapshots and profile_versions are never updated or deleted (triggers).
"""
import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from app.services.skills import VOCABULARY_VERSION

# Funnel events set the status; each maps to the status it sets.
FUNNEL_EVENTS = {
    "saved": "SAVED", "applied": "APPLIED", "online_test": "ONLINE_TEST", "interview": "INTERVIEW",
    "offer": "OFFER", "rejected": "REJECTED", "withdrawn": "WITHDRAWN", "skipped": "SKIPPED",
}
RESPONSE_EVENTS = ("recruiter_reply", "no_response_confirmed")
OUTREACH_EVENTS = ("outreach_prepared", "outreach_sent")
THUMBS_EVENTS = ("thumbs_up", "thumbs_down", "thumbs_cleared")
NOTE_EVENTS = ("removed_from_results",)
EVENT_TYPES = (*FUNNEL_EVENTS, *RESPONSE_EVENTS, *OUTREACH_EVENTS, *THUMBS_EVENTS, *NOTE_EVENTS, "undone")
SOURCES = ("user", "derived", "import", "migration")
STATUSES = tuple(FUNNEL_EVENTS.values())

FEATURE_SCHEMA_VERSION = "features-v1"
# No versioned scoring file exists yet (decision Q5); the ranker is named by this constant.
RANKER_VERSION = "v1"

_REPO = Path(__file__).resolve().parents[2]
_APPEND_ONLY = ("job_events", "job_snapshots", "profile_versions")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@lru_cache(maxsize=1)
def ranker_version() -> str:
    """"v1" plus the short git SHA when git and the repository are available, else "v1"."""
    try:
        done = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=_REPO, capture_output=True,
                              text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return RANKER_VERSION
    sha = done.stdout.strip()
    return f"{RANKER_VERSION}+{sha}" if sha else RANKER_VERSION


def _quoted(values) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE profile_versions (
        cv_version TEXT PRIMARY KEY,             -- sha256 of the canonical profile JSON
        profile_json TEXT NOT NULL,
        created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE job_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL REFERENCES career_jobs(id),
        captured_at TEXT NOT NULL,
        job_json TEXT NOT NULL,                  -- the job exactly as stored at capture time
        job_sha256 TEXT NOT NULL,
        cv_version TEXT REFERENCES profile_versions(cv_version),   -- NULL when unknown (backfill)
        feature_schema_version TEXT,
        vocabulary_version TEXT,
        ranker_version TEXT,
        model_version TEXT,
        features_json TEXT NOT NULL,
        captured_late INTEGER NOT NULL DEFAULT 0 CHECK (captured_late IN (0, 1)))""")
    conn.execute("CREATE INDEX job_snapshots_job ON job_snapshots(job_id, id)")
    conn.execute(f"""CREATE TABLE job_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL REFERENCES career_jobs(id),
        application_id TEXT REFERENCES career_applications(id),
        event_type TEXT NOT NULL CHECK (event_type IN ({_quoted(EVENT_TYPES)})),
        occurred_at TEXT NOT NULL,
        occurred_at_exact INTEGER NOT NULL DEFAULT 1 CHECK (occurred_at_exact IN (0, 1)),
        recorded_at TEXT NOT NULL,
        source TEXT NOT NULL CHECK (source IN ({_quoted(SOURCES)})),
        request_id TEXT UNIQUE,
        undoes_event_id INTEGER REFERENCES job_events(id),
        snapshot_id INTEGER REFERENCES job_snapshots(id),
        context_json TEXT,
        note TEXT NOT NULL DEFAULT '',
        CHECK ((event_type = 'undone') = (undoes_event_id IS NOT NULL)))""")
    conn.execute("CREATE INDEX job_events_job ON job_events(job_id, id)")
    conn.execute("CREATE INDEX job_events_application ON job_events(application_id, id)")
    for table in _APPEND_ONLY:
        for action in ("UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table}
                             BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END""")
    for column in ("applied_via TEXT", "effort_minutes INTEGER", "follow_up_at TEXT"):
        conn.execute(f"ALTER TABLE career_applications ADD COLUMN {column}")


def _legacy_status(status: str, applied_at: str | None) -> tuple[str, str]:
    """The new status for a legacy value, and a note naming the old value when it changed."""
    if status in STATUSES:
        return status, ""
    if status == "DISCOVERED":
        return "SAVED", "legacy status DISCOVERED folded into SAVED"
    if status in ("OUTREACH_PREPARED", "OUTREACH_SENT"):
        target = "APPLIED" if applied_at else "SAVED"
        return target, f"legacy status {status} became {target}; outreach stays in outreach_state"
    return status, f"unmapped legacy status {status} kept in the status column"


def _backfill(conn: sqlite3.Connection) -> None:
    """Estimated events and one late snapshot for every application that has a job."""
    stamp = _now()
    rows = conn.execute("""SELECT a.*, j.job_json FROM career_applications a
                           JOIN career_jobs j ON j.id = a.job_id ORDER BY a.created_at, a.id""").fetchall()
    for row in rows:
        snapshot = conn.execute(
            """INSERT INTO job_snapshots (job_id, captured_at, job_json, job_sha256, features_json, captured_late)
               VALUES (?, ?, ?, ?, '{}', 1)""",
            (row["job_id"], stamp, row["job_json"], _sha256(row["job_json"]))).lastrowid
        status, note = _legacy_status(row["status"], row["applied_at"])
        sequence = [("saved", row["created_at"])]
        if row["applied_at"]:
            sequence.append(("applied", row["applied_at"]))
        if status in STATUSES and status not in ("SAVED", "APPLIED"):
            sequence.append((status.lower(), row["updated_at"]))
        for index, (event_type, occurred_at) in enumerate(sequence):
            conn.execute(
                """INSERT INTO job_events (job_id, application_id, event_type, occurred_at, occurred_at_exact,
                   recorded_at, source, snapshot_id, note) VALUES (?, ?, ?, ?, 0, ?, 'migration', ?, ?)""",
                (row["job_id"], row["id"], event_type, occurred_at, stamp, snapshot,
                 note if index == len(sequence) - 1 else ""))
        if status != row["status"]:
            conn.execute("UPDATE career_applications SET status=? WHERE id=?", (status, row["id"]))


def migrate_v3(conn: sqlite3.Connection) -> None:
    _create_tables(conn)
    _backfill(conn)


def _inserted(cursor: sqlite3.Cursor) -> int:
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite returned no row id for an insert")
    return cursor.lastrowid


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def profile_version(conn: sqlite3.Connection, profile: dict) -> str:
    """The cv_version of a profile, storing the profile the first time it is seen."""
    payload = _canonical(profile)
    cv_version = _sha256(payload)
    conn.execute("INSERT OR IGNORE INTO profile_versions VALUES (?, ?, ?)", (cv_version, payload, _now()))
    return cv_version


def capture_snapshot(conn: sqlite3.Connection, job_id: str, *, profile: dict, features: dict) -> int:
    """A snapshot of the job as stored now; an identical latest snapshot is reused."""
    row = conn.execute("SELECT job_json FROM career_jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise ValueError("Job not found")
    values = (job_id, row["job_json"], _sha256(row["job_json"]), profile_version(conn, profile),
              FEATURE_SCHEMA_VERSION, str(VOCABULARY_VERSION), ranker_version(), _canonical(features))
    latest = conn.execute("""SELECT id, job_id, job_json, job_sha256, cv_version, feature_schema_version,
                             vocabulary_version, ranker_version, features_json FROM job_snapshots
                             WHERE job_id=? AND captured_late=0 ORDER BY id DESC LIMIT 1""", (job_id,)).fetchone()
    if latest is not None and tuple(latest)[1:] == values:
        return latest["id"]
    return _inserted(conn.execute(
        """INSERT INTO job_snapshots (job_id, job_json, job_sha256, cv_version, feature_schema_version,
           vocabulary_version, ranker_version, features_json, captured_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (*values, _now())))


def append_event(conn: sqlite3.Connection, job_id: str, event_type: str, *, application_id: str | None = None,
                 occurred_at: str | None = None, occurred_at_exact: bool = True, source: str = "user",
                 request_id: str | None = None, undoes_event_id: int | None = None, snapshot_id: int | None = None,
                 context: dict | None = None, note: str = "") -> dict:
    """Append one event; the caller's transaction also updates any cache."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown event type: {event_type}")
    if source not in SOURCES:
        raise ValueError(f"Unknown event source: {source}")
    if (event_type == "undone") != (undoes_event_id is not None):
        raise ValueError("An undone event must name the event it undoes, and only it may")
    if conn.execute("SELECT 1 FROM career_jobs WHERE id=?", (job_id,)).fetchone() is None:
        raise ValueError("Job not found")
    stamp = _now()
    identity = _inserted(conn.execute(
        """INSERT INTO job_events (job_id, application_id, event_type, occurred_at, occurred_at_exact, recorded_at,
           source, request_id, undoes_event_id, snapshot_id, context_json, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (job_id, application_id, event_type, occurred_at or stamp, int(occurred_at_exact), stamp, source,
         request_id, undoes_event_id, snapshot_id, _canonical(context) if context is not None else None, note)))
    return dict(conn.execute("SELECT * FROM job_events WHERE id=?", (identity,)).fetchone())


def events_for_job(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM job_events WHERE job_id=? ORDER BY id", (job_id,))]


def _standing(events: list[dict]) -> list[dict]:
    """Events in recorded order, without undo markers and the events they cancel."""
    undone = {event["undoes_event_id"] for event in events if event["event_type"] == "undone"}
    return [event for event in sorted(events, key=lambda item: item["id"])
            if event["event_type"] != "undone" and event["id"] not in undone]


def status_from_events(events: list[dict]) -> str | None:
    """The status set by the latest funnel event that is not undone; None when there is none."""
    funnel = [event for event in _standing(events) if event["event_type"] in FUNNEL_EVENTS]
    return FUNNEL_EVENTS[funnel[-1]["event_type"]] if funnel else None


def first_applied_at(events: list[dict]) -> str | None:
    applied = [event for event in _standing(events) if event["event_type"] == "applied"]
    return applied[0]["occurred_at"] if applied else None


# Live relevance labels (docs/M2_PLAN.md §4). A separate scale from the graded 0-3 batch labels; the
# two are never converted into each other.
THUMBS_SCALE = "thumbs"
THUMBS_RUBRIC_VERSION = "thumbs-v1"
THUMB_LABELS = {"up": "thumbs_up", "down": "thumbs_down", "clear": "thumbs_cleared"}


def current_thumb_event(events: list[dict]) -> dict | None:
    """The standing thumbs_up or thumbs_down event that is the job's current label, if any."""
    thumbs = [event for event in _standing(events) if event["event_type"] in THUMBS_EVENTS]
    return thumbs[-1] if thumbs and thumbs[-1]["event_type"] != "thumbs_cleared" else None


def thumb_label(event: dict | None) -> str | None:
    return {"thumbs_up": "up", "thumbs_down": "down"}.get(event["event_type"]) if event else None


# Reaching any of these stages is what "shortlisted" means (docs/M2_PLAN.md §2).
SHORTLIST_EVENTS = ("online_test", "interview", "offer")


def standing_types(events: list[dict]) -> set[str]:
    """The types of the events that are not undone."""
    return {event["event_type"] for event in _standing(events)}


def is_shortlisted(events: list[dict]) -> bool:
    """True once an online test, interview or offer stands in the log; a later rejection keeps it true,
    a recruiter reply alone does not count. Derived at read time, never stored."""
    return not standing_types(events).isdisjoint(SHORTLIST_EVENTS)


# A response ends censoring; it is any of these after the first application (docs/M2_PLAN.md §1.1).
RESPONSE_TYPES = ("recruiter_reply", "online_test", "interview", "offer", "rejected")


def response_state(events: list[dict], *, now: datetime, window_days: int) -> str | None:
    """RESPONDED, NO_RESPONSE or PENDING_CENSORED for an application; None if it was never applied.

    PENDING_CENSORED is unknown, never negative. Derived at read time, never stored.
    """
    standing = _standing(events)
    applied = [event for event in standing if event["event_type"] == "applied"]
    if not applied:
        return None
    after = [event for event in standing if event["id"] > applied[0]["id"]]
    if any(event["event_type"] in RESPONSE_TYPES for event in after):
        return "RESPONDED"
    if any(event["event_type"] == "no_response_confirmed" for event in after):
        return "NO_RESPONSE"
    try:
        applied_at = datetime.fromisoformat(applied[0]["occurred_at"])
    except (TypeError, ValueError):
        return "PENDING_CENSORED"   # an unreadable legacy time stays unknown, never negative
    if applied_at.tzinfo is None:
        applied_at = applied_at.replace(tzinfo=timezone.utc)
    return "NO_RESPONSE" if now - applied_at >= timedelta(days=window_days) else "PENDING_CENSORED"
