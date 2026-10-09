"""BAK1: backups of the tracker database (app/tracker/backup.py). Temporary databases and fictional rows only."""
import contextlib
import gc
import importlib.util
import io
import os
import sqlite3
import tempfile
import unittest
from collections import namedtuple
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.tracker import backup, store

Usage = namedtuple("Usage", "total used free")
LOW = Usage(100 * 1024**3, 99 * 1024**3, 1 * 1024**3)


class BackupCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(directory.name)
        self.db = self.root / "tracker.sqlite3"
        self.url = store.sqlite_url(self.db)
        self.addCleanup(store.dispose_all)
        store.upgrade(self.url)
        self.folder = self.root / "backups" / "tracker"
        roomy = patch.object(backup.shutil, "disk_usage", return_value=Usage(100 * 1024**3, 0, 100 * 1024**3))
        roomy.start()                                    # the result never depends on this computer's free space
        self.addCleanup(roomy.stop)

    def add_company(self, conn: sqlite3.Connection, name: str) -> None:
        conn.execute("INSERT INTO companies (id, owner_id, name, company_key, notes, created_at) VALUES (?, 'local', ?, ?, '', '2026-10-09')",
                     (name, name, name.lower()))

    def companies(self, path: Path) -> int:
        with closing(sqlite3.connect(path)) as conn:
            return conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]

    def copies(self) -> list[Path]:
        return sorted(self.folder.glob("tracker-*.sqlite3"))


class BackupTests(BackupCase):
    def test_the_default_folder_is_beside_the_database_and_the_environment_overrides_it(self):
        self.assertEqual(backup.folder(self.db), self.folder)
        with patch.dict(os.environ, {"CAREER_AGENT_BACKUP_DIR": str(self.root / "elsewhere")}):
            self.assertEqual(backup.folder(self.db), self.root / "elsewhere")

    def test_a_backup_made_while_a_write_is_open_is_a_whole_copy_of_the_committed_rows(self):
        with closing(sqlite3.connect(self.db)) as writer:
            self.add_company(writer, "Committed Corp")
            writer.commit()
            writer.execute("BEGIN IMMEDIATE")
            self.add_company(writer, "Uncommitted Corp")
            report = backup.backup(self.db)
            writer.rollback()
        copy = Path(report["backup"])
        self.assertEqual(copy.parent, self.folder)
        self.assertEqual(self.companies(copy), 1)
        with closing(sqlite3.connect(copy)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(list(self.folder.glob("*.partial")), [])

    def test_only_the_newest_seven_backups_are_kept(self):
        made = [Path(backup.backup(self.db)["backup"]) for _ in range(9)]
        self.assertEqual(self.copies(), made[-7:])

    def test_other_files_in_the_folder_are_never_removed(self):
        self.folder.mkdir(parents=True)
        (self.folder / "notes.txt").write_text("mine", encoding="utf-8")
        for _ in range(8):
            backup.backup(self.db)
        self.assertTrue((self.folder / "notes.txt").exists())

    def test_a_copy_that_fails_the_integrity_check_is_removed_and_reported(self):
        with patch.object(backup, "_integrity", return_value="row 3 missing from index"):
            with self.assertRaisesRegex(RuntimeError, "integrity"):
                backup.backup(self.db)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_a_drive_below_two_gigabytes_free_is_skipped_with_a_message(self):
        with patch.object(backup.shutil, "disk_usage", return_value=LOW):
            report = backup.backup(self.db)
        self.assertIsNone(report["backup"])
        self.assertIn("2 GB", report["message"])
        self.assertEqual(self.copies(), [])

    def test_a_missing_database_is_not_created(self):
        report = backup.backup(self.root / "absent.sqlite3")
        self.assertIsNone(report["backup"])
        self.assertFalse((self.root / "absent.sqlite3").exists())


class RestoreCheckTests(BackupCase):
    def test_the_newest_backup_restores_with_the_same_counts_and_the_live_file_is_untouched(self):
        with closing(sqlite3.connect(self.db)) as conn:
            self.add_company(conn, "Example Corp")
            conn.commit()
        backup.backup(self.db)
        before = self.db.read_bytes()
        report = backup.restore_check(self.db)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["restored"], report["live"])
        self.assertEqual(set(report["live"]), {"applications", "application_events"})
        self.assertEqual(self.db.read_bytes(), before)

    def test_different_counts_are_reported(self):
        backup.backup(self.db)
        with patch.object(backup, "_counts", side_effect=[{"applications": 2, "application_events": 5},
                                                          {"applications": 1, "application_events": 5}]):
            report = backup.restore_check(self.db)
        self.assertFalse(report["ok"])

    def test_a_missing_tracker_file_is_reported_not_raised(self):
        backup.backup(self.db)
        report = backup.restore_check(self.root / "absent.sqlite3", self.folder)
        self.assertFalse(report["ok"])
        self.assertIn("No tracker database", report["message"])
        self.assertFalse((self.root / "absent.sqlite3").exists())

    def test_no_backup_yet_is_reported(self):
        report = backup.restore_check(self.db)
        self.assertFalse(report["ok"])
        self.assertIn("No backup", report["message"])


class BeforeMigrationTests(BackupCase):
    def pending(self) -> None:
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE alembic_version SET version_num = '0000_older'")
            conn.commit()

    def test_a_new_or_current_database_is_not_backed_up(self):
        store.upgrade(self.url)
        self.assertEqual(self.copies(), [])

    def test_a_pending_migration_is_backed_up_first(self):
        self.pending()
        with patch.object(store.command, "upgrade", side_effect=lambda *_: self.assertEqual(len(self.copies()), 1)) as applied:
            store.upgrade(self.url)
        applied.assert_called_once()

    def test_nothing_is_applied_when_the_backup_is_skipped(self):
        self.pending()
        with patch.object(backup.shutil, "disk_usage", return_value=LOW), patch.object(store.command, "upgrade") as applied:
            with self.assertRaisesRegex(RuntimeError, "not applied"):
                store.upgrade(self.url)
        applied.assert_not_called()


class ScriptTests(BackupCase):
    def test_backup_and_restore_check_commands(self):
        spec = importlib.util.spec_from_file_location("tracker_admin", Path(__file__).resolve().parents[1] / "scripts" / "tracker_admin.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        output = io.StringIO()
        with patch.object(store, "DB_PATH", self.db), contextlib.redirect_stdout(output):
            codes = [script.main(["backup"]), script.main(["restore-check"])]
            with patch.object(backup.shutil, "disk_usage", return_value=LOW):
                codes.append(script.main(["backup"]))
        self.assertEqual(codes, [0, 0, 1])
        self.assertEqual(len(self.copies()), 1)
        with patch.object(store, "DB_PATH", self.root / "absent.sqlite3"), contextlib.redirect_stdout(output):
            self.assertEqual(script.main(["backup"]), 0)               # a new install has nothing to back up
        self.assertFalse((self.root / "absent.sqlite3").exists())
        # A failure is one line on stdout and exit code 1: a traceback on stderr would end run_daily_sync.ps1 before the sync.
        errors = io.StringIO()
        with patch.object(store, "DB_PATH", self.db), patch.object(backup, "_integrity", return_value="damaged"), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(script.main(["backup"]), 1)
        self.assertIn("Backup failed", output.getvalue())
        self.assertEqual(errors.getvalue(), "")
