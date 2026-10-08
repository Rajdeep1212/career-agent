"""TRK1: the tracker's own database (docs/TRACKER_PLAN.md sections 2 and 7). Alembic migrations up and down, an
append-only event log, owner scoping, and two idempotent imports. Temporary databases and fictional applications only."""
import gc
import hashlib
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa

from app.storage import career_store, db
from app.tracker import importers, models, store

TABLES = {"users", "companies", "contacts", "cv_versions", "applications", "application_events", "reminders"}
POSTGRES_URL = os.environ.get("TRACKER_TEST_POSTGRES_URL")
M2_TIME = "2026-09-20T10:00:00+00:00"
JOB = {"company": "Example Corp", "title": "Graduate AI Engineer", "location": "Pune, India", "description": "Freshers welcome. Python.",
       "application_url": "https://jobs.example.com/1", "source": "Company Radar", "posted_date": "2026-09-20",
       "match": {"explanation": "SENTINEL-CV-LINE from the candidate's CV"}}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class StoreCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(directory.name)
        self.url = store.sqlite_url(self.root / "tracker.sqlite3")
        self.addCleanup(store.dispose_all)

    def tables(self) -> set[str]:
        with store.engine(self.url).connect() as conn:
            return set(sa.inspect(conn).get_table_names()) - {"alembic_version"}

    def count(self, table: str) -> int:
        with store.engine(self.url).connect() as conn:
            return conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


class MigrationTests(StoreCase):
    def test_upgrade_creates_the_trk1_tables_and_downgrade_removes_them(self):
        store.upgrade(self.url)
        self.assertEqual(self.tables(), TABLES)
        store.upgrade(self.url)                                    # a second upgrade changes nothing
        self.assertEqual(self.tables(), TABLES)
        store.downgrade(self.url)
        self.assertEqual(self.tables(), set())
        store.upgrade(self.url)                                    # and it comes back
        self.assertEqual(self.tables(), TABLES)

    def test_the_migration_and_the_models_describe_the_same_columns(self):
        store.upgrade(self.url)
        with store.engine(self.url).connect() as conn:
            inspector = sa.inspect(conn)
            for name, table in models.Base.metadata.tables.items():
                self.assertEqual({column["name"] for column in inspector.get_columns(name)}, {column.name for column in table.columns}, name)
        self.assertEqual(set(models.Base.metadata.tables), TABLES)

    def test_there_is_one_fixed_local_user(self):
        store.upgrade(self.url)
        with store.engine(self.url).connect() as conn:
            rows = conn.execute(sa.text("SELECT id, role FROM users")).all()
        self.assertEqual(rows, [(store.LOCAL_USER_ID, "student")])

    def test_every_user_owned_table_has_a_required_owner_id(self):
        for name, table in models.Base.metadata.tables.items():
            if name == "users":
                continue
            self.assertIn("owner_id", table.columns, name)
            self.assertFalse(table.columns["owner_id"].nullable, name)
            self.assertEqual([key.target_fullname for key in table.columns["owner_id"].foreign_keys], ["users.id"], name)

    def test_the_event_log_refuses_update_and_delete(self):
        store.upgrade(self.url)
        with store.session(self.url) as session:
            application = models.Application(id="a1", owner_id=store.LOCAL_USER_ID, identity="x", title="Engineer", company_name="Example Corp",
                                             status="APPLIED", created_at="2026-10-01T00:00:00+00:00", updated_at="2026-10-01T00:00:00+00:00")
            session.add(application)
            session.flush()
            session.add(models.ApplicationEvent(application_id="a1", owner_id=store.LOCAL_USER_ID, event_type="applied",
                                                occurred_at="2026-10-01T00:00:00+00:00", recorded_at="2026-10-01T00:00:00+00:00",
                                                source="user", request_id="r1"))
            session.commit()
        for statement in ("UPDATE application_events SET note = 'changed'", "DELETE FROM application_events"):
            with store.engine(self.url).begin() as conn, self.assertRaises(sa.exc.DBAPIError) as caught:
                conn.execute(sa.text(statement))
            self.assertIn("append-only", str(caught.exception))
        self.assertEqual(self.count("application_events"), 1)

    def test_bad_statuses_and_repeated_request_ids_are_refused_by_the_database(self):
        store.upgrade(self.url)
        base = dict(owner_id=store.LOCAL_USER_ID, title="Engineer", company_name="Example Corp", created_at="t", updated_at="t")
        with store.session(self.url) as session, self.assertRaises(sa.exc.IntegrityError):
            session.add(models.Application(id="a1", identity="x", status="DREAMING", **base))
            session.commit()
        with store.session(self.url) as session, self.assertRaises(sa.exc.IntegrityError):
            session.add_all([models.Application(id="a1", identity="same", status="SAVED", **base),
                             models.Application(id="a2", identity="same", status="SAVED", **base)])
            session.commit()

    @unittest.skipUnless(POSTGRES_URL, "Postgres migration tests skipped: set TRACKER_TEST_POSTGRES_URL to a throwaway database. "
                                       "They need the postgres Docker image, which is used only when Docker is already running with its data root off C:.")
    def test_upgrade_downgrade_and_triggers_on_postgres(self):
        store.downgrade(POSTGRES_URL)
        store.upgrade(POSTGRES_URL)
        with store.engine(POSTGRES_URL).connect() as conn:
            self.assertEqual(set(sa.inspect(conn).get_table_names()) - {"alembic_version"}, TABLES)
        store.downgrade(POSTGRES_URL)


class M2ImportTests(StoreCase):
    def setUp(self):
        super().setUp()
        self.agent = self.root / "agent.sqlite3"
        patcher = patch.object(career_store, "DB_PATH", self.agent)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)
        self.job_id = career_store.upsert_job(JOB)
        self.old_id = "m2-application-1"
        # M2 history as the retired M2 tracker left it; the app no longer writes these tables (TRK3c).
        with career_store._connection() as conn:
            conn.execute("INSERT INTO career_applications (id, job_id, status, notes, created_at, updated_at, applied_at, applied_via) "
                         "VALUES (?, ?, 'ONLINE_TEST', '', ?, ?, ?, 'portal')", (self.old_id, self.job_id, M2_TIME, M2_TIME, M2_TIME))
        self.m2_event("applied", "req-1")
        self.m2_event("online_test", "req-2")
        career_store.upsert_job({**JOB, "title": "Saved only, never applied", "application_url": "https://jobs.example.com/2"})
        gc.collect()
        store.upgrade(self.url)

    def m2_event(self, event_type: str, request_id: str, status: str | None = None) -> None:
        with career_store._connection() as conn:
            conn.execute("INSERT INTO job_events (job_id, application_id, event_type, occurred_at, occurred_at_exact, recorded_at, source, "
                         "request_id) VALUES (?, ?, ?, ?, 1, ?, 'user', ?)", (self.job_id, self.old_id, event_type, M2_TIME, M2_TIME, request_id))
            if status:
                conn.execute("UPDATE career_applications SET status=? WHERE id=?", (status, self.old_id))

    def run_import(self, **options):
        return importers.import_m2(self.agent, self.url, backup_root=self.root / "backups", **options)

    def test_applications_events_and_snapshots_are_copied_and_the_old_database_is_untouched(self):
        before = sha(self.agent)
        report = self.run_import()
        self.assertEqual(sha(self.agent), before)
        self.assertEqual((report["m2_applications"], report["applications_in_tracker"]), (1, 1))
        self.assertEqual(report["m2_events"], report["events_in_tracker"])
        self.assertGreaterEqual(report["m2_events"], 2)
        self.assertTrue(Path(report["backup"]).exists())
        with store.engine(self.url).connect() as conn:
            row = conn.execute(sa.text("SELECT id, owner_id, status, title, company_name, url, channel, identity, snapshot_json "
                                       "FROM applications")).one()
            types = [value for (value,) in conn.execute(sa.text("SELECT event_type FROM application_events ORDER BY id"))]
            sources = {value for (value,) in conn.execute(sa.text("SELECT source FROM application_events"))}
        self.assertEqual((row.id, row.owner_id, row.status, row.title, row.company_name, row.channel),
                         (self.old_id, store.LOCAL_USER_ID, "ONLINE_TEST", "Graduate AI Engineer", "Example Corp", "portal"))
        self.assertTrue(row.identity.startswith("m2:"))
        self.assertEqual(types[:2], ["applied", "online_test"])
        self.assertEqual(sources, {"import"})
        self.assertIn("Freshers welcome", row.snapshot_json)
        self.assertNotIn("SENTINEL-CV-LINE", row.snapshot_json)            # the stored match is not part of the job post
        self.assertEqual(self.count("companies"), 1)

    def test_a_second_run_changes_no_counts_and_a_later_event_is_picked_up(self):
        first = self.run_import()
        second = self.run_import()
        for key in ("applications_in_tracker", "events_in_tracker"):
            self.assertEqual(first[key], second[key])
        self.assertEqual((second["applications_added"], second["events_added"]), (0, 0))
        self.m2_event("interview", "req-3", status="INTERVIEW")      # recorded in the old tables in between
        gc.collect()
        third = self.run_import()
        self.assertEqual((third["applications_added"], third["events_added"]), (0, 1))
        with store.engine(self.url).connect() as conn:
            self.assertEqual(conn.execute(sa.text("SELECT status FROM applications")).scalar_one(), "INTERVIEW")

    def test_a_dry_run_writes_nothing_and_makes_no_backup(self):
        report = self.run_import(dry_run=True)
        self.assertEqual((report["m2_applications"], report["applications_added"], self.count("applications")), (1, 1, 0))
        self.assertIsNone(report["backup"])
        self.assertFalse((self.root / "backups").exists())

    def test_an_absent_old_database_imports_nothing_and_says_so(self):
        report = importers.import_m2(self.root / "missing.sqlite3", self.url, backup_root=self.root / "backups")
        self.assertEqual(report["m2_applications"], 0)
        self.assertIn("Nothing to import", report["message"])


class CsvImportTests(StoreCase):
    HEADER = "company,role,url,source,channel,applied_on,cv_version,status,status_date,next_follow_up,notes"

    def setUp(self):
        super().setUp()
        store.upgrade(self.url)
        self.csv = self.root / "applications.csv"

    def write(self, *rows):
        self.csv.write_text("\n".join([self.HEADER, *rows]) + "\n", encoding="utf-8")

    def test_the_template_has_exactly_the_agreed_columns(self):
        template = self.root / "tracker" / "applications_template.csv"
        importers.write_template(template)
        self.assertEqual(template.read_text(encoding="utf-8").strip(), self.HEADER)
        self.assertEqual(",".join(importers.CSV_COLUMNS), self.HEADER)
        importers.write_template(template)                                  # never overwrites a file that is there
        self.assertEqual(template.read_text(encoding="utf-8").strip(), self.HEADER)

    def test_an_absent_or_empty_file_imports_nothing_and_says_so(self):
        for prepare in (lambda: None, lambda: self.csv.write_text("", encoding="utf-8"), lambda: self.write()):
            prepare()
            report = importers.import_csv(self.csv, self.url)
            self.assertEqual((report["rows"], report["applications_added"]), (0, 0))
            self.assertIn("Nothing to import", report["message"])
        self.assertEqual(self.count("applications"), 0)

    def test_a_dry_run_reports_and_writes_nothing_then_the_import_writes_once(self):
        self.write("Example Corp,Graduate AI Engineer,https://jobs.example.com/1,Company Radar,portal,2026-09-25,cv-v3,ONLINE_TEST,2026-09-30,2026-10-07,Referred by a senior",
                   "Other Labs,Data Analyst,,LinkedIn alert,referral,2026-09-28,,applied,,,")
        dry = importers.import_csv(self.csv, self.url, dry_run=True)
        self.assertEqual((dry["rows"], dry["valid"], dry["applications_added"], dry["problems"]), (2, 2, 2, []))
        self.assertEqual(self.count("applications"), 0)
        first = importers.import_csv(self.csv, self.url)
        second = importers.import_csv(self.csv, self.url)
        self.assertEqual((first["applications_added"], first["events_added"]), (2, 3))
        self.assertEqual((second["applications_added"], second["events_added"]), (0, 0))
        self.assertEqual((self.count("applications"), self.count("application_events"), self.count("companies"), self.count("cv_versions")),
                         (2, 3, 2, 1))
        with store.engine(self.url).connect() as conn:
            row = conn.execute(sa.text("SELECT status, applied_at, channel, source, next_follow_up_at, notes, owner_id FROM applications "
                                       "WHERE company_name = 'Example Corp'")).one()
            events = conn.execute(sa.text("SELECT event_type, occurred_at, source FROM application_events ORDER BY id")).all()
        self.assertEqual(tuple(row), ("ONLINE_TEST", "2026-09-25", "portal", "Company Radar", "2026-10-07", "Referred by a senior",
                                      store.LOCAL_USER_ID))
        self.assertEqual([tuple(event) for event in events], [("applied", "2026-09-25", "import"), ("online_test", "2026-09-30", "import"),
                                                              ("applied", "2026-09-28", "import")])

    def test_one_bad_row_stops_the_whole_import_and_names_the_line(self):
        self.write("Example Corp,Engineer,,,,2026-09-25,,APPLIED,,,", ",Engineer,,,,,,APPLIED,,,", "Other Labs,Analyst,,,,25/09/2026,,DREAMING,,,")
        report = importers.import_csv(self.csv, self.url)
        self.assertEqual((report["rows"], report["valid"], report["applications_added"]), (3, 1, 0))
        self.assertEqual([line for line, _ in report["problems"]], [3, 4, 4])
        self.assertIn("company is empty", report["problems"][0][1])
        self.assertEqual(self.count("applications"), 0)

    def test_a_wrong_header_is_refused(self):
        self.csv.write_text("company,role\nExample Corp,Engineer\n", encoding="utf-8")
        report = importers.import_csv(self.csv, self.url)
        self.assertEqual(report["applications_added"], 0)
        self.assertIn("header", report["problems"][0][1])


class ScriptTests(unittest.TestCase):
    def test_the_script_migrates_writes_the_template_and_says_when_there_is_nothing_to_import(self):
        import contextlib
        import importlib.util
        import io
        spec = importlib.util.spec_from_file_location("tracker_admin", Path(__file__).resolve().parents[1] / "scripts" / "tracker_admin.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.addCleanup(gc.collect)
            with patch.object(store, "DB_PATH", root / "tracker.sqlite3"), patch.object(script, "TEMPLATE", root / "tracker" / "t.csv"),                     patch.object(career_store, "DB_PATH", root / "agent.sqlite3"):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    codes = [script.main(["template"]), script.main(["migrate"]), script.main(["import-m2"]),
                             script.main(["import-csv", "--file", str(root / "absent.csv")])]
                store.dispose_all()
                self.assertEqual(codes, [0, 0, 0, 0])
                self.assertTrue((root / "tracker" / "t.csv").exists())
                self.assertTrue((root / "tracker.sqlite3").exists())
                self.assertEqual(output.getvalue().count("Nothing to import"), 2)
                self.assertFalse((root / "agent.sqlite3").exists())            # looking for the old database does not create it


class LiveFilesTests(unittest.TestCase):
    def test_the_tracker_file_is_its_own_database_beside_the_others(self):
        from app.core.config import settings
        self.assertEqual(store.DB_PATH, Path(settings.data_dir) / "tracker.sqlite3")
        self.assertNotEqual(store.DB_PATH, career_store.DB_PATH)

    def test_reading_the_old_database_opens_it_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.sqlite3"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TABLE t (x)")
                conn.commit()
            with closing(importers.open_read_only(path)) as conn, self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO t VALUES (1)")


if __name__ == "__main__":
    unittest.main()
