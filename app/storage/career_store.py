"""Stored jobs, sessions, contacts, outreach links and thumbs in data/agent.sqlite3.

Applications are in the tracker (data/tracker.sqlite3) since TRK3c; career_applications and the application and
outreach events of job_events are kept as history and never written. Thumbs and removal notes stay here.
"""
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args
from uuid import uuid4

from app.core.config import settings
from app.models.career import ApplicationStatus, ContactCandidate
from app.models.schemas import JobPosting
from app.services.job_identity import job_identity
from app.storage import career_events, db


DB_PATH = Path(settings.data_dir) / 'agent.sqlite3'
_STATUSES = get_args(ApplicationStatus)
Snapshot = tuple[dict, dict]   # (profile, L0 features) captured with an event


def _now():
    return datetime.now(timezone.utc).isoformat()


_V1_TABLES = (
    """CREATE TABLE IF NOT EXISTS career_sessions (
        id TEXT PRIMARY KEY, intent_json TEXT NOT NULL, response_json TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS career_jobs (
        id TEXT PRIMARY KEY, job_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS career_applications (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES career_jobs(id),
        status TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, applied_at TEXT,
        outreach_state TEXT NOT NULL DEFAULT 'NONE')""",
    """CREATE TABLE IF NOT EXISTS career_contacts (
        id TEXT PRIMARY KEY, company TEXT NOT NULL, contact_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS career_contacts_company ON career_contacts(company COLLATE NOCASE)",
    """CREATE TABLE IF NOT EXISTS career_outreach (
        draft_id INTEGER PRIMARY KEY, application_id TEXT NOT NULL REFERENCES career_applications(id),
        contact_id TEXT REFERENCES career_contacts(id),
        short_message TEXT NOT NULL, created_at TEXT NOT NULL, sent_at TEXT)""",
)


def _career_v1(conn):
    for statement in _V1_TABLES:
        conn.execute(statement)


def _outreach_contact_v2(conn):
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(career_outreach)').fetchall()}
    if 'contact_id' not in columns:
        conn.execute('ALTER TABLE career_outreach ADD COLUMN contact_id TEXT')


def _outreach_tracker_v4(conn):
    """career_outreach links drafts to tracker applications: no foreign key to career_applications, and the job id is
    kept on the row. Every row is copied; the job id of an old row comes from its M2 application."""
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(career_outreach)').fetchall()}
    if 'job_id' in columns:
        return
    conn.execute("""CREATE TABLE career_outreach_v4 (
        draft_id INTEGER PRIMARY KEY, application_id TEXT NOT NULL, job_id TEXT,
        contact_id TEXT REFERENCES career_contacts(id),
        short_message TEXT NOT NULL, created_at TEXT NOT NULL, sent_at TEXT)""")
    conn.execute("""INSERT INTO career_outreach_v4
        SELECT o.draft_id, o.application_id, a.job_id, o.contact_id, o.short_message, o.created_at, o.sent_at
        FROM career_outreach AS o LEFT JOIN career_applications AS a ON a.id = o.application_id""")
    conn.execute('DROP TABLE career_outreach')
    conn.execute('ALTER TABLE career_outreach_v4 RENAME TO career_outreach')


# Versions match the ids earlier releases recorded, so existing databases have nothing pending.
MIGRATIONS = [
    db.Migration('career_v1', _career_v1, 'Restore data/backups/<time>/agent.sqlite3; the tables are additive.'),
    db.Migration('career_outreach_contact_v2', _outreach_contact_v2,
                 'Restore data/backups/<time>/agent.sqlite3; SQLite cannot drop the contact_id column in place.'),
    db.Migration('career_v3_events', career_events.migrate_v3,
                 'Restore data/backups/<time>/agent.sqlite3 (taken before this migration). The event, snapshot '
                 'and profile tables are additive; the status remap is recorded in each backfilled event note.'),
    db.Migration('career_outreach_tracker_v4', _outreach_tracker_v4,
                 'Restore data/backups/<time>/agent.sqlite3 (taken before this migration); every row is copied.'),
]


@contextmanager
def _connection():
    db.ensure(DB_PATH, MIGRATIONS)
    with db.connect(DB_PATH) as connection:
        yield connection


def save_session(session_id: str | None, intent: dict, response: dict) -> str:
    identity = session_id or str(uuid4())
    stamp = _now()
    with _connection() as conn:
        conn.execute('''INSERT INTO career_sessions VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET intent_json=excluded.intent_json,
                        response_json=excluded.response_json, updated_at=excluded.updated_at''',
                     (identity, json.dumps(intent), json.dumps(response), stamp, stamp))
    return identity


def get_session(identity: str) -> dict | None:
    with _connection() as conn:
        row = conn.execute('SELECT * FROM career_sessions WHERE id=?', (identity,)).fetchone()
    if row is None:
        return None
    return {'id': row['id'], 'intent': json.loads(row['intent_json']), 'response': json.loads(row['response_json']),
            'created_at': row['created_at'], 'updated_at': row['updated_at']}


def upsert_job(job: dict) -> str:
    identity = job_identity(JobPosting.model_validate(job))
    stamp = _now()
    payload = json.dumps(job)
    with _connection() as conn:
        conn.execute('''INSERT INTO career_jobs VALUES (?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET job_json=excluded.job_json, updated_at=excluded.updated_at''',
                     (identity, payload, stamp, stamp))
    return identity


def get_job(identity: str) -> dict | None:
    with _connection() as conn:
        row = conn.execute('SELECT job_json FROM career_jobs WHERE id=?', (identity,)).fetchone()
    return json.loads(row['job_json']) if row else None


def capture_snapshot(job_id: str, *, profile: dict, features: dict) -> int:
    """Snapshot the job as stored now, with the CV and L0 features used (docs/M2_PLAN.md §1.3)."""
    with _connection() as conn:
        return career_events.capture_snapshot(conn, job_id, profile=profile, features=features)


def record_event(job_id: str, event_type: str, **fields) -> dict:
    """Append one event to the job's log (docs/M2_PLAN.md §1.1)."""
    with _connection() as conn:
        return career_events.append_event(conn, job_id, event_type, **fields)


def job_events(job_id: str) -> list[dict]:
    with _connection() as conn:
        return career_events.events_for_job(conn, job_id)


def note_removed_from_results(job_id: str, source: str) -> dict | None:
    """A note in the log when a job with history is removed from results; its history is kept, and a
    job with no history records nothing (docs/M2_PLAN.md decision Q7)."""
    tracked = _tracked_id(job_id)
    with _connection() as conn:
        if tracked is None and not career_events.events_for_job(conn, job_id):
            return None
        return career_events.append_event(
            conn, job_id, 'removed_from_results', application_id=_m2_application_id(conn, job_id),
            context={'tracker_application_id': tracked} if tracked else None,
            note=f'Removed from results (source: {source}). Tracker history kept; this is not a withdrawal.')


def _m2_application_id(conn, job_id: str) -> str | None:
    # job_events.application_id references career_applications (read here, never written); the tracker link is the job id.
    row = conn.execute('SELECT id FROM career_applications WHERE job_id=?', (job_id,)).fetchone()
    return row['id'] if row else None


class RequestConflictError(ValueError):
    """A request_id already used for a different action."""


def _replayed(conn, request_id: str, job_id: str, event_type: str) -> dict | None:
    """The event an earlier request with this id recorded; a conflict if that was another action."""
    row = conn.execute('SELECT * FROM job_events WHERE request_id=?', (request_id,)).fetchone()
    if row is None:
        return None
    if (row['job_id'], row['event_type'], row['undoes_event_id']) != (job_id, event_type, None):
        raise RequestConflictError('This request id was already used for a different action.')
    return dict(row)


def record_thumb(job_id: str, label: str, *, request_id: str, context: dict | None = None,
                 snapshot: Snapshot | None = None) -> dict:
    """A live thumbs up/down label on any stored job, saved or not (docs/M2_PLAN.md §4). The latest
    label wins and `clear` removes it; a repeat records nothing. Labels never change ranking or the
    tracker."""
    if label not in career_events.THUMB_LABELS:
        raise ValueError('The label must be up, down or clear')
    event_type = career_events.THUMB_LABELS[label]
    with _connection() as conn:
        if conn.execute('SELECT 1 FROM career_jobs WHERE id=?', (job_id,)).fetchone() is None:
            raise LookupError('Job not found')
        events = career_events.events_for_job(conn, job_id)
        current = career_events.thumb_label(career_events.current_thumb_event(events))
        if _replayed(conn, request_id, job_id, event_type) is not None:
            return {'job_id': job_id, 'label': current, 'already_recorded': False, 'replayed': True}
        if (label == 'clear' and current is None) or label == current:
            return {'job_id': job_id, 'label': current, 'already_recorded': True, 'replayed': False}
        snapshot_id = (career_events.capture_snapshot(conn, job_id, profile=snapshot[0], features=snapshot[1])
                       if snapshot else None)
        details = {key: value for key, value in (context or {}).items() if value is not None}
        details.update(scale=career_events.THUMBS_SCALE, rubric_version=career_events.THUMBS_RUBRIC_VERSION,
                       ranker_version=career_events.ranker_version())
        career_events.append_event(conn, job_id, event_type, application_id=_m2_application_id(conn, job_id),
                                   request_id=request_id, snapshot_id=snapshot_id, context=details)
        return {'job_id': job_id, 'label': None if label == 'clear' else label, 'already_recorded': False,
                'replayed': False}


def _labelled_jobs(conn) -> list[tuple[str, dict]]:
    """(job id, current thumbs event) for every job that has a current label."""
    job_ids = [row['job_id'] for row in conn.execute(
        "SELECT DISTINCT job_id FROM job_events WHERE event_type IN ('thumbs_up', 'thumbs_down') ORDER BY job_id")]
    found = []
    for job_id in job_ids:
        event = career_events.current_thumb_event(career_events.events_for_job(conn, job_id))
        if event is not None:
            found.append((job_id, event))
    return found


def thumbs() -> dict[str, str]:
    """The current label of every labelled job."""
    with _connection() as conn:
        return {job_id: career_events.thumb_label(event) or '' for job_id, event in _labelled_jobs(conn)}


def thumb_export_rows() -> list[dict]:
    """One row per current label for the evaluation harness. The CV appears only as its version hash."""
    rows = []
    with _connection() as conn:
        for job_id, event in _labelled_jobs(conn):
            context = json.loads(event['context_json'] or '{}')
            snapshot = conn.execute('SELECT * FROM job_snapshots WHERE id=?', (event['snapshot_id'],)).fetchone()
            job = json.loads(snapshot['job_json']) if snapshot else {}
            rows.append({
                'job_id': job_id, 'label': career_events.thumb_label(event),
                'scale': context.get('scale'), 'rubric_version': context.get('rubric_version'),
                'labelled_at': event['occurred_at'], 'event_id': event['id'],
                'search_session_id': context.get('search_session_id'), 'position': context.get('position'),
                'ranker_version': context.get('ranker_version'),
                'snapshot_id': event['snapshot_id'],
                'job_sha256': snapshot['job_sha256'] if snapshot else None,
                'cv_version': snapshot['cv_version'] if snapshot else None,
                'vocabulary_version': snapshot['vocabulary_version'] if snapshot else None,
                'captured_at': snapshot['captured_at'] if snapshot else None,
                'title': job.get('title'), 'company': job.get('company'), 'location': job.get('location'),
                'source': job.get('source'), 'description': job.get('description'),
            })
    return rows


# Applications live in the tracker (data/tracker.sqlite3, /api/v1) since TRK3c. These functions keep the shape
# the chat tools and the outreach workflow use; career_applications and the M2 event log are history, never written.

def _tracker():
    from app.tracker import service, store    # imported late: some tests load this module under a stub config
    store.ensure()
    return service, store.LOCAL_USER_ID


def _tracked_id(job_id: str) -> str | None:
    """The tracker application for a stored job, if there is one."""
    from sqlalchemy import select

    from app.tracker import store
    from app.tracker.models import Application
    _, owner = _tracker()
    with store.session() as session:
        return session.scalar(select(Application.id).where(Application.owner_id == owner, Application.job_id == job_id))


def _status(value):
    if value not in _STATUSES:
        raise ValueError('Unsupported application status')


def _outreach_state(outreach: list[dict]) -> str:
    if any(item['sent_at'] for item in outreach):
        return 'SENT'
    return 'PREPARED' if outreach else 'NONE'


def _compatible(detail: dict) -> dict:
    """A tracker application with its stored job (or the post the tracker kept) and its outreach drafts."""
    with _connection() as conn:
        job = (conn.execute('SELECT job_json FROM career_jobs WHERE id=?', (detail['job_id'],)).fetchone()
               if detail['job_id'] else None)
        outreach = [dict(row) for row in conn.execute(
            'SELECT * FROM career_outreach WHERE application_id=? ORDER BY created_at, draft_id', (detail['id'],))]
    return {**{key: detail[key] for key in ('id', 'job_id', 'status', 'notes', 'applied_at', 'created_at', 'updated_at')},
            'job': json.loads(job['job_json']) if job else detail['snapshot'],
            'timeline': detail['timeline'], 'outreach': outreach, 'outreach_state': _outreach_state(outreach)}


def save_application(job_id: str, status='SAVED', notes='') -> dict:
    """Track a stored job in the tracker; an application that exists already is returned unchanged."""
    _status(status)
    service, owner = _tracker()
    if status not in service.CREATE_STATUSES:
        raise ValueError(f"A new application starts as {' or '.join(service.CREATE_STATUSES)}")
    job = get_job(job_id)
    if job is None:
        raise ValueError('Job not found')
    try:
        detail, _ = service.create_application(
            owner, f'career-store:{uuid4()}', stored_job_id=job_id, title=str(job.get('title') or ''),
            company=str(job.get('company') or ''), url=job.get('application_url'), location=job.get('location'),
            description=job.get('description'), source=job.get('source'), notes=notes, status=status)
    except service.TrackerError as exc:
        raise ValueError(str(exc)) from exc
    return _compatible(detail)


def list_applications() -> list:
    service, owner = _tracker()
    return [_compatible(service.get_application(owner, row['id'])) for row in service.list_applications(owner)]


def get_application(identity: str) -> dict | None:
    service, owner = _tracker()
    try:
        return _compatible(service.get_application(owner, identity))
    except service.NotFound:
        return None


def update_application(identity: str, status: str | None = None, notes: str | None = None) -> dict | None:
    """Change notes, and record a status change as a tracker event; the same status again records nothing."""
    if status is not None:
        _status(status)
    service, owner = _tracker()
    try:
        current = service.get_application(owner, identity)
        if status is not None and status != current['status']:
            service.append_event(owner, identity, f'career-store:{uuid4()}', service.STATUS_EVENT[status])
        if notes is not None:
            service.update_application(owner, identity, {'notes': notes})
    except service.NotFound:
        return None
    except service.TrackerError as exc:
        raise ValueError(str(exc)) from exc
    return get_application(identity)


def save_contact(contact: ContactCandidate) -> dict:
    identity, stamp = str(uuid4()), _now()
    payload = contact.model_dump(mode='json')
    with _connection() as conn:
        conn.execute('INSERT INTO career_contacts VALUES (?, ?, ?, ?)', (identity, contact.company.strip(), json.dumps(payload), stamp))
    return {**payload, 'id': identity, 'created_at': stamp}


def list_contacts(company: str) -> list:
    with _connection() as conn:
        rows = conn.execute('SELECT * FROM career_contacts WHERE company=? COLLATE NOCASE ORDER BY created_at DESC', (company.strip(),)).fetchall()
    return [{**json.loads(row['contact_json']), 'id': row['id'], 'created_at': row['created_at']} for row in rows]


def get_contact(identity: str) -> dict | None:
    with _connection() as conn:
        row = conn.execute('SELECT * FROM career_contacts WHERE id=?', (identity,)).fetchone()
    return ({**json.loads(row['contact_json']), 'id': row['id'], 'created_at': row['created_at']}
            if row else None)


def get_outreach_for_draft(draft_id: int) -> dict | None:
    with _connection() as conn:
        row = conn.execute('SELECT * FROM career_outreach WHERE draft_id=?', (draft_id,)).fetchone()
    return dict(row) if row else None


def link_outreach(application_id, draft_id, short_message, contact_id=None) -> None:
    """Link a draft to a tracker application. Outreach is an event, not a status (docs/M2_PLAN.md decision Q2)."""
    service, owner = _tracker()
    try:
        application = service.get_application(owner, application_id)
    except service.NotFound as exc:
        raise ValueError('Application not found') from exc
    with _connection() as conn:
        if contact_id is not None and conn.execute(
                'SELECT id FROM career_contacts WHERE id=?', (contact_id,)).fetchone() is None:
            raise ValueError('Contact not found')
        existing = conn.execute('SELECT application_id FROM career_outreach WHERE draft_id=?', (draft_id,)).fetchone()
        if existing and existing['application_id'] != application_id:
            raise ValueError('Draft already belongs to another application')
        conn.execute('''INSERT INTO career_outreach
                        (draft_id, application_id, job_id, contact_id, short_message, created_at, sent_at)
                        VALUES (?, ?, ?, ?, ?, ?, NULL)
                        ON CONFLICT(draft_id) DO UPDATE SET
                            short_message=excluded.short_message,
                            contact_id=excluded.contact_id''',
                     (draft_id, application_id, application['job_id'], contact_id, short_message, _now()))
        if existing is None:     # inside the block: if the tracker refuses, the link is rolled back
            service.record_outreach(owner, application_id, 'outreach_prepared', draft_id)


def mark_outreach_sent(draft_id) -> None:
    """Called only after the existing mail sender confirms sending."""
    with _connection() as conn:
        row = conn.execute('SELECT application_id, sent_at FROM career_outreach WHERE draft_id=?', (draft_id,)).fetchone()
        if row is None:
            return
        if row['sent_at'] is None:
            service, owner = _tracker()
            try:
                service.record_outreach(owner, row['application_id'], 'outreach_sent', draft_id)
            except service.NotFound:
                pass     # a link from before the tracker import, to an application it never received
        conn.execute('UPDATE career_outreach SET sent_at=COALESCE(sent_at, ?) WHERE draft_id=?', (_now(), draft_id))
