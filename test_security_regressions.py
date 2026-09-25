"""Security regressions for M1A (docs/AUDIT_AND_ROADMAP.md, S1-S5 and B6)."""
import gc
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.storage import attachment_store, career_store, history, preference_store, profile_store

LOCAL = {'Origin': 'http://localhost:8010'}


def _pdf(text='TEST MEMBER\nB.Tech 2025\nPython SQL'):
    with fitz.open() as document:
        document.new_page().insert_text((72, 72), text)
        return document.tobytes()


class _IsolatedApp(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(gc.collect)
        self.directory = Path(temporary.name)
        for module, name, value in (
            (history, 'DB_PATH', self.directory / 'history.sqlite3'),
            (attachment_store, 'DB_PATH', self.directory / 'agent.sqlite3'),
            (attachment_store, 'UPLOAD_DIR', self.directory / 'uploads'),
            (career_store, 'DB_PATH', self.directory / 'career.sqlite3'),
            (profile_store, 'PROFILE_PATH', self.directory / 'profile.json'),
            (preference_store, 'PREFERENCES_PATH', self.directory / 'preferences.json'),
        ):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(app, base_url='http://localhost:8010', raise_server_exceptions=False)
        self.addCleanup(self.client.close)


class AttachmentUploadTests(_IsolatedApp):
    """S1: uploads need the local origin and never return a filesystem path."""

    def _upload(self, headers=None):
        return self.client.post('/attachments', files={'file': ('resume.pdf', _pdf(), 'application/pdf')},
                                headers=headers or {})

    def test_cross_site_upload_is_rejected_and_writes_nothing(self):
        for headers in ({}, {'Origin': 'https://attacker.example'}):
            self.assertEqual(self._upload(headers).status_code, 403)
        uploads = self.directory / 'uploads'
        self.assertFalse(uploads.exists() and any(uploads.iterdir()))

    def test_local_upload_omits_the_stored_path(self):
        response = self._upload(LOCAL)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('stored_path', response.json())
        self.assertNotIn(str(self.directory), response.text)
        self.assertEqual(set(response.json()), {'id', 'original_name', 'mime_type', 'size_bytes'})


class LocalOriginTests(_IsolatedApp):
    """S3: remaining browser mutations require the exact local origin."""

    def test_cv_parse_requires_local_origin(self):
        files = {'file': ('cv.pdf', _pdf(), 'application/pdf')}
        self.assertEqual(self.client.post('/cv/parse', files=files).status_code, 403)
        self.assertEqual(self.client.post('/cv/parse', files=files, headers=LOCAL).status_code, 200)

    def test_gmail_disconnect_requires_local_origin(self):
        with patch('app.main.delete_token') as delete_token:
            self.assertEqual(self.client.delete('/auth/google/disconnect').status_code, 403)
            delete_token.assert_not_called()
            self.assertEqual(self.client.delete('/auth/google/disconnect', headers=LOCAL).status_code, 200)
            delete_token.assert_called_once_with('gmail')


if __name__ == '__main__':
    unittest.main()
