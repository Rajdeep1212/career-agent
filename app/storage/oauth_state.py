import secrets
import hashlib
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import settings


DB_PATH = Path(settings.data_dir) / "agent.sqlite3"


def _ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_states (
                state TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def create_state() -> str:
    _ensure_db()
    state = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO oauth_states(state, created_at) VALUES (?, ?)",
            (state, now),
        )
        conn.commit()

    return state


def consume_state(state: str, max_age_minutes: int = 10) -> bool:
    _ensure_db()

    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT created_at FROM oauth_states WHERE state = ?",
            (state,),
        ).fetchone()

        if row:
            conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            conn.commit()

    if not row:
        return False

    created = datetime.fromisoformat(row[0])
    age = datetime.now(timezone.utc) - created
    return age <= timedelta(minutes=max_age_minutes)


# Browser-bound states are separate from legacy Gmail states so adding LinkedIn
# neither migrates nor invalidates an existing Google authorization in progress.
def _bound_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS oauth_bound_states (
            state_hash TEXT PRIMARY KEY, provider TEXT NOT NULL,
            browser_hash TEXT NOT NULL, expires_at REAL NOT NULL
        )""")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_bound_state(provider: str, browser: str, ttl: int = 600) -> str:
    _bound_db()
    state = secrets.token_urlsafe(32)
    now = time.time()
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute("DELETE FROM oauth_bound_states WHERE expires_at <= ?", (now,))
        conn.execute("INSERT INTO oauth_bound_states VALUES (?, ?, ?, ?)",
                     (_digest(state), provider, _digest(browser), now + ttl))
    return state


def consume_bound_state(provider: str, state: str, browser: str) -> bool:
    _bound_db()
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        # Serialize read/delete across workers: exactly one callback can succeed.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""SELECT expires_at FROM oauth_bound_states
            WHERE state_hash=? AND provider=? AND browser_hash=?""",
            (_digest(state), provider, _digest(browser))).fetchone()
        if row:
            conn.execute("DELETE FROM oauth_bound_states WHERE state_hash=?", (_digest(state),))
    return bool(row and row[0] > time.time())


def clear_bound_states(provider: str) -> None:
    _bound_db()
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute("DELETE FROM oauth_bound_states WHERE provider=?", (provider,))
