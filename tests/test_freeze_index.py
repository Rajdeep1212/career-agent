"""M2 commit 7: a frozen, read-only copy of the Radar index for labelling (docs/M2_PLAN.md §5).
A tiny temporary database stands in for the index; live data is never touched."""
import hashlib
import importlib.util
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location('freeze_index', ROOT / 'scripts' / 'freeze_index.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path, table: str) -> list[tuple]:
    with closing(sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)) as conn:
        return conn.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()


class FreezeIndexTests(unittest.TestCase):
    def setUp(self):
        self.freeze_index = _module()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(self._make_writable, Path(directory.name))
        self.root = Path(directory.name)
        self.source = self.root / 'radar.sqlite3'
        with closing(sqlite3.connect(self.source)) as conn, conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('CREATE TABLE radar_jobs (key TEXT PRIMARY KEY, job_json TEXT NOT NULL, status TEXT NOT NULL)')
            conn.execute('CREATE TABLE radar_sync_runs (id INTEGER PRIMARY KEY, company_id TEXT NOT NULL)')
            conn.executemany('INSERT INTO radar_jobs VALUES (?, ?, ?)',
                             [(f'example:{index}', '{"title": "NLP Engineer"}', 'ACTIVE') for index in range(5)])
            conn.execute("INSERT INTO radar_sync_runs (company_id) VALUES ('example')")
        self.frozen = self.root / 'eval' / 'frozen'

    @staticmethod
    def _make_writable(root: Path):
        # Frozen files are read-only; Windows cannot delete them until the attribute is cleared.
        for path in root.rglob('*'):
            if path.is_file():
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)

    def freeze(self, stamp='20261001T000000Z'):
        return self.freeze_index.freeze(self.source, self.frozen, stamp=stamp)

    def test_the_copy_holds_the_same_rows_and_the_source_is_untouched(self):
        before = _sha(self.source)
        folder = self.freeze()
        copy = folder / 'radar.sqlite3'
        self.assertEqual(folder, self.frozen / '20261001T000000Z')
        for table in ('radar_jobs', 'radar_sync_runs'):
            self.assertEqual(_rows(copy, table), _rows(self.source, table), table)
        self.assertEqual(_sha(self.source), before)
        self.assertEqual(sorted(path.name for path in folder.iterdir()), ['manifest.json', 'radar.sqlite3'],
                         'no -wal or -shm files travel with the frozen copy')

    def test_the_manifest_describes_the_copy(self):
        folder = self.freeze()
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['sha256'], _sha(folder / 'radar.sqlite3'))
        self.assertEqual(manifest['row_counts'], {'radar_jobs': 5, 'radar_sync_runs': 1})
        self.assertEqual(manifest['source_path'], str(self.source.resolve()))
        self.assertEqual(manifest['frozen_at_utc'], '20261001T000000Z')
        self.assertEqual(manifest['file'], 'radar.sqlite3')
        self.assertIn('git_sha', manifest)
        self.assertTrue(manifest['git_sha'] is None or 4 <= len(manifest['git_sha']) <= 40)

    def test_the_copy_is_read_only(self):
        folder = self.freeze()
        copy = folder / 'radar.sqlite3'
        self.assertFalse(os.access(copy, os.W_OK))
        with self.assertRaises(PermissionError):
            copy.open('ab').close()
        with closing(sqlite3.connect(copy)) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO radar_sync_runs (company_id) VALUES ('later')")
        self.assertEqual(json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))['sha256'], _sha(copy))

    def test_a_second_run_into_the_same_folder_is_refused(self):
        folder = self.freeze()
        before = _sha(folder / 'radar.sqlite3')
        with self.assertRaisesRegex(FileExistsError, 'already exists'):
            self.freeze()
        self.assertEqual(_sha(folder / 'radar.sqlite3'), before)
        self.assertEqual(self.freeze(stamp='20261002T000000Z').name, '20261002T000000Z')

    def test_a_missing_source_or_demo_mode_is_refused(self):
        with self.assertRaisesRegex(FileNotFoundError, 'not found'):
            self.freeze_index.freeze(self.root / 'absent.sqlite3', self.frozen, stamp='20261001T000000Z')
        with patch.object(self.freeze_index.settings, 'demo_mode', True):
            with self.assertRaisesRegex(RuntimeError, 'demo'):
                self.freeze()
        self.assertFalse(self.frozen.exists(), 'a refused run creates nothing')

    def test_the_default_source_is_the_radar_index(self):
        from app.storage import radar_store
        self.assertEqual(self.freeze_index.default_source(), radar_store.DB_PATH)


if __name__ == '__main__':
    unittest.main()
