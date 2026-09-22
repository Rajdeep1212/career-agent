"""Email drafts with explicit approval and one atomic send claim per draft."""
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.services.email_composer import validate_header, validate_recipient


DB_PATH = Path(settings.data_dir) / 'agent.sqlite3'


def _now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH, timeout=15)) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS email_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
                attachment_id TEXT, status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL, approved_at TEXT, sent_at TEXT, gmail_message_id TEXT
            )''')
            yield conn


def _ensure():
    with _connection():
        pass


def _get(conn, draft_id):
    row = conn.execute('SELECT * FROM email_drafts WHERE id=?', (draft_id,)).fetchone()
    return dict(row) if row else None


def create_draft(recipient: str, subject: str, body: str, attachment_id: str | None):
    recipient = validate_recipient(recipient)
    subject = validate_header(subject, 'Subject')
    with _connection() as conn:
        cur = conn.execute('''INSERT INTO email_drafts
            (recipient, subject, body, attachment_id, status, created_at)
            VALUES (?, ?, ?, ?, 'draft', ?)''', (recipient, subject, body, attachment_id, _now()))
        return _get(conn, cur.lastrowid)


def update_draft_content(draft_id: int, *, subject: str | None = None, body: str | None = None):
    """Edit reviewable content only while a draft is still unapproved."""
    if subject is not None:
        subject = validate_header(subject, 'Subject')
    if body is not None:
        body = body.strip()
        if not body or len(body) > 100000:
            raise ValueError('Email body is invalid')
    if subject is None and body is None:
        return get_draft(draft_id)
    with _connection() as conn:
        row = _get(conn, draft_id)
        if not row or row['status'] != 'draft':
            return None
        conn.execute(
            'UPDATE email_drafts SET subject=?, body=? WHERE id=? AND status=\'draft\'',
            (subject if subject is not None else row['subject'],
             body if body is not None else row['body'], draft_id),
        )
        return _get(conn, draft_id)


def get_draft(draft_id: int):
    with _connection() as conn:
        return _get(conn, draft_id)


def list_drafts(limit: int = 50):
    with _connection() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM email_drafts ORDER BY id DESC LIMIT ?', (limit,)).fetchall()]


def approve_draft(draft_id: int):
    with _connection() as conn:
        cur = conn.execute("UPDATE email_drafts SET status='approved', approved_at=? WHERE id=? AND status='draft'", (_now(), draft_id))
        return _get(conn, draft_id) if cur.rowcount == 1 else None


def cancel_draft(draft_id: int):
    with _connection() as conn:
        cur = conn.execute("UPDATE email_drafts SET status='cancelled' WHERE id=? AND status IN ('draft', 'approved')", (draft_id,))
        return _get(conn, draft_id) if cur.rowcount == 1 else None


def claim_draft_for_send(draft_id: int) -> dict | None:
    """Only one process/thread can change an approved draft to sending."""
    with _connection() as conn:
        cur = conn.execute("UPDATE email_drafts SET status='sending' WHERE id=? AND status='approved'", (draft_id,))
        return _get(conn, draft_id) if cur.rowcount == 1 else None


def mark_send_failed(draft_id: int) -> dict | None:
    """Sending may have succeeded remotely. Never make this draft retryable."""
    with _connection() as conn:
        conn.execute("UPDATE email_drafts SET status='send_failed' WHERE id=? AND status='sending'", (draft_id,))
        return _get(conn, draft_id)


def mark_sent(draft_id: int, gmail_message_id: str | None):
    with _connection() as conn:
        cur = conn.execute(
            "UPDATE email_drafts SET status='sent', sent_at=?, gmail_message_id=? "
            "WHERE id=? AND status='sending'",
            (_now(), gmail_message_id, draft_id),
        )
        return _get(conn, draft_id) if cur.rowcount == 1 else None
