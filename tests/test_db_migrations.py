"""Numbered migrations: backup first, one transaction each, idempotent, no silent data loss."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.storage import db


def _create_notes(conn):
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")


def _add_tag(conn):
    conn.execute("ALTER TABLE notes ADD COLUMN tag TEXT")


def _broken(conn):
    conn.execute("INSERT INTO notes(body) VALUES ('partial')")
    raise RuntimeError("migration failed half way")


M1 = db.Migration("0001_notes", _create_notes, "Restore the backup, or drop table notes.")
M2 = db.Migration("0002_notes_tag", _add_tag, "Restore the backup; SQLite cannot drop the column in place.")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "store.sqlite3"
        db.reset_cache()
        self.addCleanup(db.reset_cache)

    def _versions(self):
        with closing(sqlite3.connect(self.path)) as conn:
            return [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]

    def test_applies_in_order_and_is_idempotent(self):
        self.assertEqual(db.migrate(self.path, [M1, M2]), ["0001_notes", "0002_notes_tag"])
        self.assertEqual(db.migrate(self.path, [M1, M2]), [])
        self.assertEqual(self._versions(), ["0001_notes", "0002_notes_tag"])

    def test_connections_use_wal(self):
        with db.connect(self.path) as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_existing_data_is_backed_up_before_a_pending_migration(self):
        db.migrate(self.path, [M1])
        with db.connect(self.path) as conn:
            conn.execute("INSERT INTO notes(body) VALUES ('keep me')")
        db.migrate(self.path, [M1, M2])
        backups = list((self.path.parent / "backups").rglob("store.sqlite3"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as conn:
            self.assertEqual(conn.execute("SELECT body FROM notes").fetchall(), [("keep me",)])
        with db.connect(self.path) as conn:
            self.assertEqual([tuple(row) for row in conn.execute("SELECT body, tag FROM notes")], [("keep me", None)])

    def test_no_backup_when_nothing_is_pending(self):
        db.migrate(self.path, [M1])
        db.migrate(self.path, [M1])
        self.assertFalse((self.path.parent / "backups").exists())

    def test_failed_migration_rolls_back_and_is_not_recorded(self):
        db.migrate(self.path, [M1])
        with self.assertRaises(RuntimeError):
            db.migrate(self.path, [M1, db.Migration("0002_broken", _broken, "Nothing to undo.")])
        self.assertEqual(self._versions(), ["0001_notes"])
        with db.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 0)

    def test_failed_backup_aborts_before_any_change(self):
        db.migrate(self.path, [M1])
        with patch.object(db, "_backup", side_effect=OSError("disk full")), self.assertRaises(OSError):
            db.migrate(self.path, [M1, M2])
        self.assertEqual(self._versions(), ["0001_notes"])

    def test_every_migration_needs_a_rollback_note(self):
        with self.assertRaises(ValueError):
            db.Migration("0003_no_note", _add_tag, "")

    def test_ensure_runs_once_per_process_but_again_if_the_file_is_gone(self):
        with patch.object(db, "migrate", wraps=db.migrate) as spy:
            db.ensure(self.path, [M1])
            db.ensure(self.path, [M1])
            self.assertEqual(spy.call_count, 1)
            self.path.unlink()
            db.ensure(self.path, [M1])
            self.assertEqual(spy.call_count, 2)


if __name__ == "__main__":
    unittest.main()
