import sqlite3
import uuid
from contextlib import closing
from pathlib import Path, PureWindowsPath

from app.core.config import settings


DB_PATH = Path(settings.data_dir) / "agent.sqlite3"
UPLOAD_DIR = Path(settings.upload_dir)

# The one allow-list for attachments, used when a file is stored (/cv/upload, POST /attachments),
# when a draft refers to it and when it is sent. It also fixes the MIME type each is sent as, so
# nothing is guessed from a file name.
ATTACHMENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_BYTES = 5 * 1024 * 1024


class AttachmentRejectedError(ValueError):
    """An attachment that may not be stored or sent; the message says what to do instead."""


def max_megabytes() -> int:
    return MAX_BYTES // (1024 * 1024)


def check_attachment_type(filename: str) -> str:
    """The lower-case extension of an allowed attachment name."""
    suffix = Path(filename).suffix.lower()
    if suffix not in ATTACHMENT_TYPES:
        raise AttachmentRejectedError(
            f"{Path(filename).name} is a {suffix or 'file without an extension'}: only PDF or .docx files can be "
            "attached. Upload the CV again as PDF or .docx."
        )
    return suffix


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
    suffix = check_attachment_type(filename)
    if len(content) > MAX_BYTES:
        raise AttachmentRejectedError(f"Attachment exceeds the {max_megabytes()} MB limit.")
    _ensure()

    attachment_id = uuid.uuid4().hex
    stored_path = UPLOAD_DIR / f"{attachment_id}{suffix}"
    stored_path.write_bytes(content)

    mime = ATTACHMENT_TYPES[suffix]

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


def attachable(attachment_id: str) -> dict:
    """The stored row for an attachment a draft may refer to: it exists and its type is allowed."""
    attachment = get_attachment(attachment_id)
    if attachment is None:
        raise AttachmentRejectedError("The attached file no longer exists. Upload the CV again.")
    check_attachment_type(attachment["original_name"])
    return attachment


def _upload_file(name: str) -> Path:
    """A stored file by its bare name, inside the current upload folder once both are resolved."""
    if not name or name in (".", "..") or any(mark in name for mark in ("/", "\\", ":")):
        raise AttachmentRejectedError("The attached file is not a plain name in the upload folder and was refused.")
    folder = UPLOAD_DIR.resolve()
    try:
        path = (folder / name).resolve(strict=True)
    except OSError as error:
        raise AttachmentRejectedError("The attached file is missing. Upload the CV again.") from error
    if path.parent != folder or not path.is_file():
        raise AttachmentRejectedError("The attached file is outside the upload folder and was refused.")
    return path


def sendable_attachment(attachment_id: str) -> dict:
    """Name, MIME type and bytes of an attachment, re-checked before every send.

    The file is found by its stored name in the current upload folder, not by the stored absolute
    path, so rows written in another checkout still work and a row can never point elsewhere.
    """
    attachment = attachable(attachment_id)
    stored_name = PureWindowsPath(attachment["stored_path"]).name
    suffix = check_attachment_type(stored_name)
    path = _upload_file(stored_name)
    too_large = AttachmentRejectedError(
        f"{attachment['original_name']} is larger than {max_megabytes()} MB and cannot be sent.")
    if path.stat().st_size > MAX_BYTES:
        raise too_large
    content = path.read_bytes()
    if len(content) > MAX_BYTES:
        raise too_large
    return {"filename": attachment["original_name"], "mime_type": ATTACHMENT_TYPES[suffix], "content": content}
