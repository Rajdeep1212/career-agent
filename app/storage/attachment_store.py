import mimetypes
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from app.core.config import settings


DB_PATH = Path(settings.data_dir) / "agent.sqlite3"
UPLOAD_DIR = Path(settings.upload_dir)

ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt"}
MAX_BYTES = 5 * 1024 * 1024


def _ensure():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS attachments (
                id TEXT PRIMARY KEY,
                original_name TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                mime_type TEXT,
                size_bytes INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def save_attachment(filename: str, content: bytes) -> dict:
    _ensure()

    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported attachment type: {suffix}. "
            f"Allowed: {sorted(ALLOWED_EXTENSIONS)}"
        )

    if len(content) > MAX_BYTES:
        raise ValueError("Attachment exceeds the 5 MB limit.")

    attachment_id = uuid.uuid4().hex
    stored_path = UPLOAD_DIR / f"{attachment_id}{suffix}"
    stored_path.write_bytes(content)

    mime, _ = mimetypes.guess_type(filename)
    mime = mime or "application/octet-stream"

    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute(
            """
            INSERT INTO attachments
            (id, original_name, stored_path, mime_type, size_bytes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                attachment_id,
                Path(filename).name,
                str(stored_path),
                mime,
                len(content),
            ),
        )
        conn.commit()

    return {
        "id": attachment_id,
        "original_name": Path(filename).name,
        "stored_path": str(stored_path),
        "mime_type": mime,
        "size_bytes": len(content),
    }


def get_attachment(attachment_id: str) -> dict | None:
    _ensure()

    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, original_name, stored_path, mime_type, size_bytes
            FROM attachments
            WHERE id = ?
            """,
            (attachment_id,),
        ).fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "original_name": row[1],
        "stored_path": row[2],
        "mime_type": row[3],
        "size_bytes": row[4],
    }
