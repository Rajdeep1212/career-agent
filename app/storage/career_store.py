"""Additive career storage sharing the agent database without touching legacy tables."""
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args
from uuid import uuid4

from app.core.config import settings
from app.models.career import ApplicationStatus, ContactCandidate
from app.models.schemas import JobPosting
from app.services.job_identity import job_identity


DB_PATH = Path(settings.data_dir) / 'agent.sqlite3'
_STATUSES = get_args(ApplicationStatus)


def _now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH, timeout=15)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        with connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY, applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS career_sessions (
                    id TEXT PRIMARY KEY, intent_json TEXT NOT NULL, response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS career_jobs (
                    id TEXT PRIMARY KEY, job_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS career_applications (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES career_jobs(id),
                    status TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, applied_at TEXT,
                    outreach_state TEXT NOT NULL DEFAULT 'NONE'
                );
                CREATE TABLE IF NOT EXISTS career_contacts (
                    id TEXT PRIMARY KEY, company TEXT NOT NULL, contact_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS career_contacts_company ON career_contacts(company COLLATE NOCASE);
                CREATE TABLE IF NOT EXISTS career_outreach (
                    draft_id INTEGER PRIMARY KEY,
                    application_id TEXT NOT NULL REFERENCES career_applications(id),
                    contact_id TEXT REFERENCES career_contacts(id),
                    short_message TEXT NOT NULL, created_at TEXT NOT NULL, sent_at TEXT
                );
            ''')
            outreach_columns = {
                row['name'] for row in connection.execute('PRAGMA table_info(career_outreach)').fetchall()
            }
            if 'contact_id' not in outreach_columns:
                connection.execute('ALTER TABLE career_outreach ADD COLUMN contact_id TEXT')
            connection.execute('INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)', ('career_v1', _now()))
            connection.execute('INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)', ('career_outreach_contact_v2', _now()))
        with connection:
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


def _application(conn, row) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    job = conn.execute('SELECT job_json FROM career_jobs WHERE id=?', (row['job_id'],)).fetchone()
    result['job'] = json.loads(job['job_json']) if job else None
    result['outreach'] = [dict(item) for item in conn.execute('SELECT * FROM career_outreach WHERE application_id=? ORDER BY created_at', (row['id'],)).fetchall()]
    return result


def _status(value):
    if value not in _STATUSES:
        raise ValueError('Unsupported application status')


def save_application(job_id: str, status='SAVED', notes='') -> dict:
    _status(status)
    stamp = _now()
    with _connection() as conn:
        if conn.execute('SELECT id FROM career_jobs WHERE id=?', (job_id,)).fetchone() is None:
            raise ValueError('Job not found')
        conn.execute('''INSERT OR IGNORE INTO career_applications
                        (id, job_id, status, notes, created_at, updated_at, applied_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                     (str(uuid4()), job_id, status, notes, stamp, stamp, stamp if status == 'APPLIED' else None))
        application = _application(conn, conn.execute('SELECT * FROM career_applications WHERE job_id=?', (job_id,)).fetchone())
        if application is None:
            raise RuntimeError('Application could not be stored')
        return application


def list_applications() -> list:
    with _connection() as conn:
        return [_application(conn, row) for row in conn.execute('SELECT * FROM career_applications ORDER BY updated_at DESC, id').fetchall()]


def get_application(identity: str) -> dict | None:
    with _connection() as conn:
        return _application(conn, conn.execute('SELECT * FROM career_applications WHERE id=?', (identity,)).fetchone())


def update_application(identity: str, status: str | None = None, notes: str | None = None) -> dict | None:
    if status is not None:
        _status(status)
    with _connection() as conn:
        row = conn.execute('SELECT * FROM career_applications WHERE id=?', (identity,)).fetchone()
        if row is None:
            return None
        stamp = _now()
        applied_at = row['applied_at'] or (stamp if status == 'APPLIED' else None)
        conn.execute('UPDATE career_applications SET status=?, notes=?, updated_at=?, applied_at=? WHERE id=?',
                     (status if status is not None else row['status'], notes if notes is not None else row['notes'], stamp, applied_at, identity))
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
        row = conn.execute('SELECT application_id FROM career_outreach WHERE draft_id=?', (draft_id,)).fetchone()
        if row is None:
            return
        stamp = _now()
        conn.execute('UPDATE career_outreach SET sent_at=COALESCE(sent_at, ?) WHERE draft_id=?', (stamp, draft_id))
        conn.execute("UPDATE career_applications SET outreach_state='SENT', updated_at=? WHERE id=?", (stamp, row['application_id']))
