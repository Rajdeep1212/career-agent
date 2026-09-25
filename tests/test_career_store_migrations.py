"""career_store runs its schema through numbered migrations, once, without data loss."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.storage import career_store, db

OLD_SCHEMA = """
CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE career_sessions (id TEXT PRIMARY KEY, intent_json TEXT NOT NULL, response_json TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE career_jobs (id TEXT PRIMARY KEY, job_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE career_applications (id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES career_jobs(id),
    status TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    applied_at TEXT, outreach_state TEXT NOT NULL DEFAULT 'NONE');
CREATE TABLE career_contacts (id TEXT PRIMARY KEY, company TEXT NOT NULL, contact_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE career_outreach (draft_id INTEGER PRIMARY KEY, application_id TEXT NOT NULL REFERENCES career_applications(id),
    short_message TEXT NOT NULL, created_at TEXT NOT NULL, sent_at TEXT);
INSERT INTO career_jobs VALUES ('job-1', '{"company": "Example", "title": "Analyst"}', 't', 't');
INSERT INTO career_applications VALUES ('app-1', 'job-1', 'APPLIED', 'keep', 't', 't', 't', 'NONE');
INSERT INTO schema_migrations VALUES ('career_v1', 't');
"""


class CareerStoreMigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "agent.sqlite3"
        patcher = patch.object(career_store, "DB_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)

    def test_fresh_database_gets_every_table(self):
        self.assertEqual(career_store.list_applications(), [])
        with closing(sqlite3.connect(self.path)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        self.assertLessEqual({"career_sessions", "career_jobs", "career_applications", "career_contacts", "career_outreach"}, tables)
        self.assertEqual(versions, {"career_v1", "career_outreach_contact_v2"})

    def test_older_database_is_upgraded_with_its_data(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript(OLD_SCHEMA)
        application = career_store.get_application("app-1")
        self.assertEqual((application["status"], application["notes"]), ("APPLIED", "keep"))
        with closing(sqlite3.connect(self.path)) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(career_outreach)")}
        self.assertIn("contact_id", columns)
        self.assertEqual(len(list((self.path.parent / "backups").rglob("agent.sqlite3"))), 1)

    def test_schema_work_happens_once_per_process(self):
        with patch.object(db, "migrate", wraps=db.migrate) as spy:
            for index in range(5):
                career_store.upsert_job({"company": "Example", "title": f"Role {index}", "location": "India"})
        self.assertEqual(spy.call_count, 1)


if __name__ == "__main__":
    unittest.main()
