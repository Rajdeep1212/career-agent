"""Seen-job history moves to the data directory without losing or touching the old file."""
import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.models.schemas import JobPosting
from app.storage import db, history

JOB = JobPosting(company="Example", title="Analyst", location="India", application_url="https://example.com/jobs/1")


def _legacy_db(path: Path):
    """The pre-migration layout: tables created ad hoc, no schema_migrations."""
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE seen_jobs (fingerprint TEXT PRIMARY KEY, company TEXT NOT NULL, title TEXT NOT NULL, "
                     "location TEXT, application_url TEXT, first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE seen_job_identities (identity TEXT PRIMARY KEY, first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.executemany("INSERT INTO seen_job_identities(identity) VALUES (?)", [(key,) for key in history.identity_keys(JOB)])


class HistoryMigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.legacy = self.root / "app_storage" / "job_history.sqlite3"
        self.legacy.parent.mkdir()
        self.new = self.root / "data" / "job_history.sqlite3"
        for name, value in (("DB_PATH", self.new), ("LEGACY_DB_PATH", self.legacy)):
            patcher = patch.object(history, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)

    def _digest(self):
        return hashlib.sha256(self.legacy.read_bytes()).hexdigest()

    def test_legacy_history_is_copied_and_left_untouched(self):
        _legacy_db(self.legacy)
        before = self._digest()
        self.assertTrue(history.is_seen(JOB))
        self.assertTrue(self.new.exists())
        self.assertEqual(self._digest(), before)
        with closing(sqlite3.connect(self.new)) as conn:
            self.assertIn("history_v1", [row[0] for row in conn.execute("SELECT version FROM schema_migrations")])

    def test_adoption_happens_once(self):
        _legacy_db(self.legacy)
        history.is_seen(JOB)
        other = JOB.model_copy(update={"application_url": "https://example.com/jobs/2"})
        with closing(sqlite3.connect(self.legacy)) as conn, conn:
            conn.executemany("INSERT INTO seen_job_identities(identity) VALUES (?)", [(key,) for key in history.identity_keys(other)])
        db.reset_cache()
        self.assertFalse(history.is_seen(other))

    def test_fresh_install_without_legacy_file(self):
        self.assertFalse(history.is_seen(JOB))
        history.mark_seen(JOB)
        self.assertTrue(history.is_seen(JOB))
        self.assertFalse(self.legacy.exists())

    def test_adoption_can_be_disabled(self):
        _legacy_db(self.legacy)
        with patch.object(history, "LEGACY_DB_PATH", None):
            self.assertFalse(history.is_seen(JOB))

    def test_default_location_is_the_data_directory(self):
        from app.core.config import settings
        self.assertEqual(history.default_db_path(), Path(settings.data_dir) / "job_history.sqlite3")


if __name__ == "__main__":
    unittest.main()
