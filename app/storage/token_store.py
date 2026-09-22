import json
from contextlib import closing
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


DB_PATH = Path(settings.data_dir) / "agent.sqlite3"


class TokenStoreError(RuntimeError):
    pass


class OAuthCancelledError(RuntimeError):
    pass


def _ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_tokens (
                provider TEXT PRIMARY KEY,
                encrypted_json BLOB NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("""CREATE TABLE IF NOT EXISTS oauth_generations (
            provider TEXT PRIMARY KEY, generation INTEGER NOT NULL
        )""")
        conn.commit()


def _fernet() -> Fernet:
    if not settings.token_encryption_key:
        raise TokenStoreError(
            "TOKEN_ENCRYPTION_KEY is not configured. "
            "Generate a Fernet key and place it in .env."
        )
    try:
        return Fernet(settings.token_encryption_key.encode("utf-8"))
    except Exception as exc:
        raise TokenStoreError("TOKEN_ENCRYPTION_KEY is invalid.") from exc


def validate_encryption_key() -> None:
    """Fail before starting an OAuth flow if encrypted persistence is unavailable."""
    _fernet()


def token_generation(provider: str) -> int:
    _ensure_db()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute("SELECT generation FROM oauth_generations WHERE provider=?", (provider,)).fetchone()
    return row[0] if row else 0


def save_token(provider: str, token_data: dict, *, expected_generation: int | None = None):
    _ensure_db()
    raw = json.dumps(token_data).encode("utf-8")
    encrypted = _fernet().encrypt(raw)

    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if expected_generation is not None:
            row = conn.execute("SELECT generation FROM oauth_generations WHERE provider=?", (provider,)).fetchone()
            if (row[0] if row else 0) != expected_generation:
                raise OAuthCancelledError("Authorization was cancelled by disconnect.")
        conn.execute(
            """
            INSERT INTO oauth_tokens(provider, encrypted_json, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(provider) DO UPDATE SET
                encrypted_json=excluded.encrypted_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (provider, encrypted),
        )
        conn.commit()


def load_token(provider: str) -> dict | None:
    _ensure_db()

    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        row = conn.execute(
            "SELECT encrypted_json FROM oauth_tokens WHERE provider = ?",
            (provider,),
        ).fetchone()

    if not row:
        return None

    try:
        raw = _fernet().decrypt(row[0])
    except InvalidToken as exc:
        raise TokenStoreError("Stored OAuth token could not be decrypted.") from exc

    return json.loads(raw.decode("utf-8"))


def delete_token(provider: str, *, invalidate_pending: bool = False):
    _ensure_db()
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if invalidate_pending:
            conn.execute("""INSERT INTO oauth_generations(provider, generation) VALUES (?, 1)
                ON CONFLICT(provider) DO UPDATE SET generation=generation+1""", (provider,))
        conn.execute("DELETE FROM oauth_tokens WHERE provider = ?", (provider,))
        conn.commit()
