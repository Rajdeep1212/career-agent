"""Cached aggregator results: the same normalized query within 12 hours costs 0 requests.

Keys are provider + normalized (role, location, country); values are the
normalized JobPosting list returned by the provider. A refresh bypasses reads
and replaces the entry. The daily JSearch query from the Radar sync is stored
under a dated key and read back by searches (see latest_daily).
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import settings
from app.models.schemas import JobPosting
from app.storage import db

DB_PATH = Path(settings.data_dir) / "search_cache.sqlite3"
TTL_HOURS = 12
DAILY_PREFIX = "daily:"


def _create_v1(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS cached_searches (
        key TEXT PRIMARY KEY, provider TEXT NOT NULL, query TEXT NOT NULL,
        jobs_json TEXT NOT NULL, fetched_at TEXT NOT NULL)""")


MIGRATIONS = [db.Migration("search_cache_v1", _create_v1,
                           "Delete data/search_cache.sqlite3; it only holds cached provider results.")]


def _connect():
    db.ensure(DB_PATH, MIGRATIONS)
    return db.connect(DB_PATH)


def _normal(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").casefold()).strip()


def cache_key(provider: str, planned) -> str:
    """provider|role|location|country, normalized so equivalent queries share an entry."""
    role = _normal(getattr(planned, "role", "") or getattr(planned, "query", ""))
    return "|".join((_normal(provider), role, _normal(getattr(planned, "location", "")), _normal(settings.search_country)))


def get(key: str, *, max_age_hours: float = TTL_HOURS, now: datetime | None = None) -> list[JobPosting] | None:
    """Cached jobs for this key if fetched within max_age_hours, else None."""
    now = now or datetime.now(timezone.utc)
    with _connect() as conn:
        row = conn.execute("SELECT jobs_json, fetched_at FROM cached_searches WHERE key=?", (key,)).fetchone()
    if row is None or now - datetime.fromisoformat(row["fetched_at"]) > timedelta(hours=max_age_hours):
        return None
    return [JobPosting.model_validate(item) for item in json.loads(row["jobs_json"])]


def put(key: str, provider: str, query: str, jobs: list[JobPosting], *, now: datetime | None = None) -> None:
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    payload = json.dumps([job.model_dump(mode="json") for job in jobs])
    with _connect() as conn:
        conn.execute("""INSERT INTO cached_searches VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET provider=excluded.provider, query=excluded.query,
                        jobs_json=excluded.jobs_json, fetched_at=excluded.fetched_at""",
                     (key, provider, query, payload, stamp))


def daily_key(provider: str, day: str) -> str:
    return f"{DAILY_PREFIX}{_normal(provider)}:{day}"


def latest_daily(provider: str) -> tuple[str, list[JobPosting]] | None:
    """(day, jobs) of the newest stored daily query for this provider, if any."""
    with _connect() as conn:
        row = conn.execute("SELECT key, jobs_json FROM cached_searches WHERE key LIKE ? ORDER BY key DESC LIMIT 1",
                           (f"{DAILY_PREFIX}{_normal(provider)}:%",)).fetchone()
    if row is None:
        return None
    return row["key"].rsplit(":", 1)[1], [JobPosting.model_validate(item) for item in json.loads(row["jobs_json"])]
