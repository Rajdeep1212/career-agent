"""Additive career storage sharing the agent database without touching legacy tables."""
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


# Versions match the ids earlier releases recorded, so existing databases have nothing pending.
MIGRATIONS = [
    db.Migration('career_v1', _career_v1, 'Restore data/backups/<time>/agent.sqlite3; the tables are additive.'),
    db.Migration('career_outreach_contact_v2', _outreach_contact_v2,
                 'Restore data/backups/<time>/agent.sqlite3; SQLite cannot drop the contact_id column in place.'),
    db.Migration('career_v3_events', career_events.migrate_v3,
                 'Restore data/backups/<time>/agent.sqlite3 (taken before this migration). The event, snapshot '
                 'and profile tables are additive; the status remap is recorded in each backfilled event note.'),
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
    with _connection() as conn:
        if not career_events.events_for_job(conn, job_id):
            return None
        application = conn.execute('SELECT id FROM career_applications WHERE job_id=?', (job_id,)).fetchone()
        return career_events.append_event(
            conn, job_id, 'removed_from_results', application_id=application['id'] if application else None,
            note=f'Removed from results (source: {source}). Tracker history kept; this is not a withdrawal.')


def _response_window() -> int:
    # Some tests import this module under a minimal settings stand-in without the field.
    return getattr(settings, 'response_window_days', 21)


def _application(conn, row) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    job = conn.execute('SELECT job_json FROM career_jobs WHERE id=?', (row['job_id'],)).fetchone()
    result['job'] = json.loads(job['job_json']) if job else None
    result['outreach'] = [dict(item) for item in conn.execute('SELECT * FROM career_outreach WHERE application_id=? ORDER BY created_at', (row['id'],)).fetchall()]
    # Derived at read time from the log, never stored (docs/M2_PLAN.md §1.1).
    events = career_events.events_for_job(conn, row['job_id'])
    result['response_state'] = career_events.response_state(events, now=datetime.now(timezone.utc),
                                                            window_days=_response_window())
    result['shortlisted'] = career_events.is_shortlisted(events)
    latest = _latest_tracker_event(events)
    result['last_event'] = ({key: latest[key] for key in ('id', 'event_type', 'occurred_at')}
                            if latest is not None else None)
    return result


_RESPONSE_COUNTS = {'RESPONDED': 'responded', 'NO_RESPONSE': 'no_response', 'PENDING_CENSORED': 'pending_censored'}


def funnel(*, now: datetime | None = None) -> dict:
    """Counts over the event log (claim level L0): how many applications reached each stage.

    An application counts as applied while an applied event stands. The shortlist rate is given over
    all applied applications, and over the resolved ones: a young application with no response is
    unknown, never negative, so it is left out of the resolved rate (docs/M2_PLAN.md §2).
    """
    now = now or datetime.now(timezone.utc)
    window = _response_window()
    counts = dict.fromkeys(('tracked', 'applied', 'responded', 'no_response', 'pending_censored',
                            'shortlisted', 'offers', 'rejected', 'withdrawn'), 0)
    with _connection() as conn:
        for row in conn.execute('SELECT job_id FROM career_applications').fetchall():
            events = career_events.events_for_job(conn, row['job_id'])
            counts['tracked'] += 1
            state = career_events.response_state(events, now=now, window_days=window)
            if state is None:
                continue
            counts['applied'] += 1
            counts[_RESPONSE_COUNTS[state]] += 1
            types = career_events.standing_types(events)
            counts['shortlisted'] += career_events.is_shortlisted(events)
            counts['offers'] += 'offer' in types
            counts['rejected'] += 'rejected' in types
            counts['withdrawn'] += 'withdrawn' in types
    resolved = counts['applied'] - counts['pending_censored']

    def rate(denominator: int) -> float | None:
        return round(counts['shortlisted'] / denominator, 4) if denominator else None

    return {
        'claim_level': 'L0',
        'note': 'Counts from your own tracker, not an estimate or a prediction.',
        'response_window_days': window,
        'counts': counts,
        'shortlist_rate': {
            'of_applied': {'shortlisted': counts['shortlisted'], 'applied': counts['applied'], 'rate': rate(counts['applied'])},
            'of_resolved': {'shortlisted': counts['shortlisted'], 'resolved': resolved, 'rate': rate(resolved)},
        },
    }


def _status(value):
    if value not in _STATUSES:
        raise ValueError('Unsupported application status')


# The funnel event that sets each status.
_STATUS_EVENTS = {status: event for event, status in career_events.FUNNEL_EVENTS.items()}
Snapshot = tuple[dict, dict]   # (profile, L0 features) captured with a status event


def _refresh_cache(conn, application_id: str, job_id: str) -> None:
    """status and applied_at are a cache of the log (docs/M2_PLAN.md §1.2). An application whose funnel
    events are all undone stays tracked as SAVED; no event is invented for it."""
    events = career_events.events_for_job(conn, job_id)
    conn.execute('UPDATE career_applications SET status=?, applied_at=?, updated_at=? WHERE id=?',
                 (career_events.status_from_events(events) or 'SAVED', career_events.first_applied_at(events),
                  _now(), application_id))


# Events recorded from the tracker's one-click buttons, and the ones Undo may cancel.
OUTCOME_EVENTS = ('recruiter_reply', 'online_test', 'interview', 'offer', 'rejected', 'withdrawn',
                  'no_response_confirmed')
_TRACKER_EVENTS = (*career_events.FUNNEL_EVENTS, *career_events.RESPONSE_EVENTS)


class RequestConflictError(ValueError):
    """A request_id already used for a different action."""


def _replayed(conn, request_id: str, job_id: str, event_type: str, undoes: int | None = None) -> dict | None:
    """The event an earlier request with this id recorded; a conflict if that was another action."""
    row = conn.execute('SELECT * FROM job_events WHERE request_id=?', (request_id,)).fetchone()
    if row is None:
        return None
    if (row['job_id'], row['event_type'], row['undoes_event_id']) != (job_id, event_type, undoes):
        raise RequestConflictError('This request id was already used for a different action.')
    return dict(row)


def _latest_tracker_event(events: list[dict]) -> dict | None:
    tracker = [event for event in career_events._standing(events) if event['event_type'] in _TRACKER_EVENTS]
    return tracker[-1] if tracker else None


def _result(conn, application_id: str, event: dict, **flags) -> dict:
    row = conn.execute('SELECT * FROM career_applications WHERE id=?', (application_id,)).fetchone()
    return {'application': _application(conn, row), 'event': event, **flags}


def _application_row(conn, application_id: str):
    row = conn.execute('SELECT * FROM career_applications WHERE id=?', (application_id,)).fetchone()
    if row is None:
        raise LookupError('Application not found')
    return row


def record_applied(job_id: str, *, request_id: str, occurred_at: str | None = None, applied_via: str | None = None,
                   effort_minutes: int | None = None, note: str = '', snapshot: Snapshot | None = None) -> dict:
    """One-click Applied (docs/M2_PLAN.md §3): creates the application if needed, never a second applied
    event while one stands, and a repeated request_id returns the first result. One transaction."""
    with _connection() as conn:
        if conn.execute('SELECT 1 FROM career_jobs WHERE id=?', (job_id,)).fetchone() is None:
            raise LookupError('Job not found')
        replay = _replayed(conn, request_id, job_id, 'applied')
        if replay is not None:
            return _result(conn, replay['application_id'], replay, already_applied=False, replayed=True)
        stamp = _now()
        conn.execute('''INSERT OR IGNORE INTO career_applications (id, job_id, status, notes, created_at, updated_at)
                        VALUES (?, ?, 'APPLIED', '', ?, ?)''', (str(uuid4()), job_id, stamp, stamp))
        row = conn.execute('SELECT * FROM career_applications WHERE job_id=?', (job_id,)).fetchone()
        standing = [event for event in career_events._standing(career_events.events_for_job(conn, job_id))
                    if event['event_type'] == 'applied']
        if standing:
            return _result(conn, row['id'], standing[0], already_applied=True, replayed=False)
        snapshot_id = (career_events.capture_snapshot(conn, job_id, profile=snapshot[0], features=snapshot[1])
                       if snapshot else None)
        event = career_events.append_event(conn, job_id, 'applied', application_id=row['id'], occurred_at=occurred_at,
                                           request_id=request_id, snapshot_id=snapshot_id, note=note)
        conn.execute('UPDATE career_applications SET applied_via=COALESCE(?, applied_via), '
                     'effort_minutes=COALESCE(?, effort_minutes) WHERE id=?', (applied_via, effort_minutes, row['id']))
        _refresh_cache(conn, row['id'], job_id)
        return _result(conn, row['id'], event, already_applied=False, replayed=False)


def record_outcome(application_id: str, event_type: str, *, request_id: str, occurred_at: str | None = None,
                   note: str = '', snapshot: Snapshot | None = None) -> dict:
    """A one-click outcome; the same outcome twice in a row records nothing."""
    if event_type not in OUTCOME_EVENTS:
        raise ValueError('Only outcome events are recorded here')
    with _connection() as conn:
        row = _application_row(conn, application_id)
        replay = _replayed(conn, request_id, row['job_id'], event_type)
        if replay is not None:
            return _result(conn, application_id, replay, already_recorded=False, replayed=True)
        latest = _latest_tracker_event(career_events.events_for_job(conn, row['job_id']))
        if latest is not None and latest['event_type'] == event_type:
            return _result(conn, application_id, latest, already_recorded=True, replayed=False)
        snapshot_id = (career_events.capture_snapshot(conn, row['job_id'], profile=snapshot[0], features=snapshot[1])
                       if snapshot else None)
        event = career_events.append_event(conn, row['job_id'], event_type, application_id=application_id,
                                           occurred_at=occurred_at, request_id=request_id, snapshot_id=snapshot_id,
                                           note=note)
        _refresh_cache(conn, application_id, row['job_id'])
        return _result(conn, application_id, event, already_recorded=False, replayed=False)


def undo_event(application_id: str, event_id: int, *, request_id: str) -> dict:
    """Cancel one tracker event of this application's job with an `undone` event; idempotent."""
    with _connection() as conn:
        row = _application_row(conn, application_id)
        target = conn.execute('SELECT * FROM job_events WHERE id=? AND job_id=?', (event_id, row['job_id'])).fetchone()
        if target is None:
            raise LookupError('Event not found for this application')
        if target['event_type'] not in _TRACKER_EVENTS:
            raise ValueError('Only tracker events can be undone here')
        replay = _replayed(conn, request_id, row['job_id'], 'undone', event_id)
        if replay is not None:
            return _result(conn, application_id, replay, already_undone=False, replayed=True)
        earlier = conn.execute("SELECT * FROM job_events WHERE event_type='undone' AND undoes_event_id=?",
                               (event_id,)).fetchone()
        if earlier is not None:
            return _result(conn, application_id, dict(earlier), already_undone=True, replayed=False)
        event = career_events.append_event(conn, row['job_id'], 'undone', application_id=application_id,
                                           request_id=request_id, undoes_event_id=event_id)
        _refresh_cache(conn, application_id, row['job_id'])
        return _result(conn, application_id, event, already_undone=False, replayed=False)


def _record_status(conn, row, status: str, snapshot: Snapshot | None) -> dict:
    snapshot_id = (career_events.capture_snapshot(conn, row['job_id'], profile=snapshot[0], features=snapshot[1])
                   if snapshot else None)
    event = career_events.append_event(conn, row['job_id'], _STATUS_EVENTS[status], application_id=row['id'],
                                       snapshot_id=snapshot_id)
    _refresh_cache(conn, row['id'], row['job_id'])
    return event


def save_application(job_id: str, status='SAVED', notes='', *, snapshot: Snapshot | None = None) -> dict:
    """Track a job; a new application records its first status event, an existing one is unchanged."""
    _status(status)
    stamp = _now()
    with _connection() as conn:
        if conn.execute('SELECT id FROM career_jobs WHERE id=?', (job_id,)).fetchone() is None:
            raise ValueError('Job not found')
        created = conn.execute('''INSERT OR IGNORE INTO career_applications
                                  (id, job_id, status, notes, created_at, updated_at, applied_at)
                                  VALUES (?, ?, ?, ?, ?, ?, NULL)''',
                               (str(uuid4()), job_id, status, notes, stamp, stamp)).rowcount
        row = conn.execute('SELECT * FROM career_applications WHERE job_id=?', (job_id,)).fetchone()
        if row is None:
            raise RuntimeError('Application could not be stored')
        if created:
            _record_status(conn, row, status, snapshot)
        application = _application(conn, conn.execute('SELECT * FROM career_applications WHERE id=?', (row['id'],)).fetchone())
        if application is None:
            raise RuntimeError('Application could not be stored')
        return application


def list_applications() -> list:
    with _connection() as conn:
        return [_application(conn, row) for row in conn.execute('SELECT * FROM career_applications ORDER BY updated_at DESC, id').fetchall()]


def get_application(identity: str) -> dict | None:
    with _connection() as conn:
        return _application(conn, conn.execute('SELECT * FROM career_applications WHERE id=?', (identity,)).fetchone())


def update_application(identity: str, status: str | None = None, notes: str | None = None, *,
                       snapshot: Snapshot | None = None) -> dict | None:
    """Change notes, and record a status change as an event; the same status again records nothing."""
    if status is not None:
        _status(status)
    with _connection() as conn:
        row = conn.execute('SELECT * FROM career_applications WHERE id=?', (identity,)).fetchone()
        if row is None:
            return None
        conn.execute('UPDATE career_applications SET notes=?, updated_at=? WHERE id=?',
                     (notes if notes is not None else row['notes'], _now(), identity))
        if status is not None and status != row['status']:
            _record_status(conn, row, status, snapshot)
        return _application(conn, conn.execute('SELECT * FROM career_applications WHERE id=?', (identity,)).fetchone())


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
        row = conn.execute('''SELECT outreach.*, applications.job_id
                              FROM career_outreach AS outreach
                              JOIN career_applications AS applications
                                ON applications.id=outreach.application_id
                              WHERE outreach.draft_id=?''', (draft_id,)).fetchone()
    return dict(row) if row else None


def link_outreach(application_id, draft_id, short_message, contact_id=None) -> None:
    with _connection() as conn:
        if conn.execute('SELECT id FROM career_applications WHERE id=?', (application_id,)).fetchone() is None:
            raise ValueError('Application not found')
        if contact_id is not None and conn.execute(
                'SELECT id FROM career_contacts WHERE id=?', (contact_id,)).fetchone() is None:
            raise ValueError('Contact not found')
        existing = conn.execute('SELECT application_id FROM career_outreach WHERE draft_id=?', (draft_id,)).fetchone()
        if existing and existing['application_id'] != application_id:
            raise ValueError('Draft already belongs to another application')
        if existing is None:
            # Outreach is an event, not a status (docs/M2_PLAN.md decision Q2); once per draft.
            job_id = conn.execute('SELECT job_id FROM career_applications WHERE id=?', (application_id,)).fetchone()['job_id']
            career_events.append_event(conn, job_id, 'outreach_prepared', application_id=application_id,
                                       context={'draft_id': draft_id})
        conn.execute('''INSERT INTO career_outreach
                        (draft_id, application_id, contact_id, short_message, created_at, sent_at)
                        VALUES (?, ?, ?, ?, ?, NULL)
                        ON CONFLICT(draft_id) DO UPDATE SET
                            short_message=excluded.short_message,
                            contact_id=excluded.contact_id''',
                     (draft_id, application_id, contact_id, short_message, _now()))
        sent = conn.execute('SELECT 1 FROM career_outreach WHERE application_id=? AND sent_at IS NOT NULL', (application_id,)).fetchone()
        conn.execute('UPDATE career_applications SET outreach_state=?, updated_at=? WHERE id=?', ('SENT' if sent else 'PREPARED', _now(), application_id))


def mark_outreach_sent(draft_id) -> None:
    """Called only after the existing mail sender confirms sending."""
    with _connection() as conn:
        row = conn.execute('''SELECT o.application_id, o.sent_at, a.job_id FROM career_outreach o
                              JOIN career_applications a ON a.id = o.application_id WHERE o.draft_id=?''',
                           (draft_id,)).fetchone()
        if row is None:
            return
        stamp = _now()
        if row['sent_at'] is None:
            career_events.append_event(conn, row['job_id'], 'outreach_sent', application_id=row['application_id'],
                                       context={'draft_id': draft_id})
        conn.execute('UPDATE career_outreach SET sent_at=COALESCE(sent_at, ?) WHERE draft_id=?', (stamp, draft_id))
        conn.execute("UPDATE career_applications SET outreach_state='SENT', updated_at=? WHERE id=?", (stamp, row['application_id']))
