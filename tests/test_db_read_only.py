"""M2-8a: a read-only open path in app/storage/db.py, so a frozen index copy can be read by the app's
own store code without a byte of it changing (docs/ROADMAP_QUEUE.md Q1). Temporary databases only."""
import hashlib
import importlib.util
import os
import sqlite3
import stat
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.models.schemas import JobPosting
from app.storage import db, radar_store

ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create(conn: sqlite3.Connection) -> None:
    conn.execute('CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
    conn.execute("INSERT INTO notes (body) VALUES ('first')")


def _later(conn: sqlite3.Connection) -> None:
    conn.execute('ALTER TABLE notes ADD COLUMN extra TEXT')


FIRST = db.Migration('notes_v1', _create, 'Restore the backup.')
SECOND = db.Migration('notes_v2', _later, 'Restore the backup.')


class _Frozen(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(self._make_writable, Path(directory.name))
        self.addCleanup(db.reset_cache)
        db.reset_cache()
        self.root = Path(directory.name)

    @staticmethod
    def _make_writable(root: Path):
        for path in root.rglob('*'):
            if path.is_file():
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)

    @staticmethod
    def seal(path: Path):
        """What scripts/freeze_index.py does to its copy: one self-contained read-only file."""
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('PRAGMA journal_mode=DELETE')
        os.chmod(path, stat.S_IREAD)
        db.reset_cache()


class ReadOnlyOpenTests(_Frozen):
    def setUp(self):
        super().setUp()
        self.path = self.root / 'frozen dir' / 'notes.sqlite3'   # a space, as in the real repo path
        db.migrate(self.path, [FIRST])
        self.seal(self.path)
        self.before = _sha(self.path)

    def unchanged(self):
        self.assertEqual(_sha(self.path), self.before)
        self.assertEqual([item.name for item in self.path.parent.iterdir()], ['notes.sqlite3'],
                         'no -wal, -shm, journal or backups folder beside the file')

    def test_a_read_only_file_cannot_be_opened_the_usual_way(self):
        with self.assertRaises(sqlite3.OperationalError):
            with db.connect(self.path):
                pass
        self.unchanged()

    def test_rows_are_read_and_not_a_byte_changes(self):
        with db.read_only(self.path):
            with db.connect(self.path) as conn:
                row = conn.execute('SELECT body FROM notes').fetchone()
                self.assertEqual(row['body'], 'first', 'rows are still addressed by column name')
                self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        self.unchanged()

    def test_a_write_is_refused(self):
        with db.read_only(self.path):
            with self.assertRaises(sqlite3.OperationalError):
                with db.connect(self.path) as conn:
                    conn.execute("INSERT INTO notes (body) VALUES ('second')")
        self.unchanged()

    def test_a_writable_file_is_not_written_either(self):
        os.chmod(self.path, stat.S_IWRITE | stat.S_IREAD)
        with db.read_only(self.path):
            with self.assertRaises(sqlite3.OperationalError):
                with db.connect(self.path) as conn:
                    conn.execute("INSERT INTO notes (body) VALUES ('second')")
        self.unchanged()

    def test_ensure_with_nothing_pending_is_a_no_op(self):
        with db.read_only(self.path):
            db.ensure(self.path, [FIRST])
            self.assertEqual(db.migrate(self.path, [FIRST]), [])
        self.unchanged()

    def test_a_pending_migration_is_refused_without_a_backup(self):
        with db.read_only(self.path):
            with self.assertRaisesRegex(db.ReadOnlyError, 'notes_v2'):
                db.ensure(self.path, [FIRST, SECOND])
        self.unchanged()

    def test_a_missing_file_is_refused_and_nothing_is_created(self):
        absent = self.root / 'absent' / 'notes.sqlite3'
        with db.read_only(absent):
            with self.assertRaises(sqlite3.OperationalError):
                with db.connect(absent):
                    pass
            with self.assertRaises(sqlite3.OperationalError):
                db.ensure(absent, [FIRST])
        self.assertFalse(absent.parent.exists())

    def test_only_the_named_file_is_read_only_and_only_inside_the_block(self):
        other = self.root / 'other.sqlite3'
        with db.read_only(self.path):
            db.migrate(other, [FIRST])
            with db.connect(other) as conn:
                conn.execute("INSERT INTO notes (body) VALUES ('second')")
        os.chmod(self.path, stat.S_IWRITE | stat.S_IREAD)
        with db.connect(self.path) as conn:
            conn.execute("INSERT INTO notes (body) VALUES ('after the block')")
        with db.connect(self.path) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 2)

    def test_the_block_ends_even_when_the_work_fails(self):
        with self.assertRaises(ValueError):
            with db.read_only(self.path):
                raise ValueError('boom')
        self.assertFalse(db.is_read_only(self.path))


class FrozenIndexTests(_Frozen):
    """The real store code over a copy made by scripts/freeze_index.py."""

    def test_the_radar_store_reads_a_frozen_copy_without_changing_it(self):
        spec = importlib.util.spec_from_file_location('freeze_index', ROOT / 'scripts' / 'freeze_index.py')
        freeze_index = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(freeze_index)
        source = self.root / 'radar.sqlite3'
        job = JobPosting(title='NLP Engineer', company='ExampleCo', location='Pune', description='Python and NLP',
                         source_job_id='1')
        with patch.object(radar_store, 'DB_PATH', source):
            radar_store.record_listing('example', [job], complete=True, today=date(2026, 10, 1))
        db.reset_cache()
        folder = freeze_index.freeze(source, self.root / 'eval' / 'frozen', stamp='20261001T000000Z')
        copy = folder / 'radar.sqlite3'
        before = _sha(copy)
        with patch.object(radar_store, 'DB_PATH', copy), db.read_only(copy):
            self.assertTrue(radar_store.has_jobs())
            self.assertEqual([found.title for found in radar_store.list_jobs()], ['NLP Engineer'])
            self.assertEqual(len(radar_store.index_entries()), 1)
            with self.assertRaises(sqlite3.OperationalError):
                radar_store.record_listing('example', [job], complete=True, today=date(2026, 10, 2))
        self.assertEqual(_sha(copy), before)
        self.assertEqual(sorted(item.name for item in folder.iterdir()), ['manifest.json', 'radar.sqlite3'])


if __name__ == '__main__':
    unittest.main()
