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


class LegacySearchEndpointTests(_IsolatedApp):
    """S2/B8: the side-effecting legacy GET is retired with 410 Gone."""

    def test_legacy_get_returns_410_without_provider_calls(self):
        with patch('app.providers.jsearch_provider.JSearchProvider.search',
                   side_effect=AssertionError('provider must not be called')):
            response = self.client.get('/jobs/search-and-rank?query=python')
        self.assertEqual(response.status_code, 410)
        self.assertIn('/agent/search', response.json()['detail'])
        self.assertFalse((self.directory / 'history.sqlite3').exists())


class AppOriginTests(unittest.TestCase):
    """S4: the trusted dashboard origin is a setting, not a constant."""

    def test_origin_check_follows_the_setting(self):
        with patch.object(settings, 'app_origin', 'http://localhost:9000'):
            with TestClient(app, base_url='http://localhost:9000') as client:
                self.assertEqual(client.put('/preferences/current', json={},
                                            headers={'Origin': 'http://localhost:8010'}).status_code, 403)
            with TestClient(app, base_url='http://localhost:8010') as client:
                self.assertEqual(client.put('/preferences/current', json={}, headers=LOCAL).status_code, 403)

    def test_loopback_ip_redirects_to_the_configured_origin(self):
        with patch.object(settings, 'app_origin', 'http://localhost:9000'):
            with TestClient(app, base_url='http://127.0.0.1:9000') as client:
                response = client.get('/app/', follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], 'http://localhost:9000/app/')

    def test_invalid_origin_setting_is_rejected(self):
        from app.core.config import Settings
        for value in ('localhost:8010', 'ftp://localhost:8010', 'http://localhost:8010/app', 'http://'):
            with self.assertRaises(ValueError, msg=value):
                Settings(app_origin=value)
        self.assertEqual(Settings(app_origin='http://localhost:9000/').app_origin, 'http://localhost:9000')


class ProfileLoadTests(_IsolatedApp):
    """B6: a corrupt profile is reported, never replaced by the fictional demo profile."""

    def _corrupt(self):
        profile_store.PROFILE_PATH.write_text('{"name": "Real Student", "skills": ', encoding='utf-8')

    def test_missing_profile_still_uses_the_labeled_demo(self):
        response = self.client.get('/profile/current')
        self.assertEqual(response.status_code, 200)
        self.assertIn('demo', response.json()['name'].lower())

    def test_corrupt_profile_is_a_visible_error(self):
        self._corrupt()
        for response in (self.client.get('/profile/current'),
                         self.client.post('/agent/search', json={'query': 'python jobs'})):
            self.assertEqual(response.status_code, 409)
            self.assertIn('could not be read', response.json()['detail'])
            self.assertNotIn('Alex Morgan', response.text)

    def test_reupload_recovers_and_keeps_a_copy_of_the_corrupt_file(self):
        self._corrupt()
        response = self.client.post('/cv/upload', files={'file': ('cv.pdf', _pdf(), 'application/pdf')},
                                    headers=LOCAL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/profile/current').json()['name'], 'Test Member')
        backups = list(self.directory.glob('profile.corrupt-*.json'))
        self.assertEqual(len(backups), 1)
        self.assertIn('Real Student', backups[0].read_text(encoding='utf-8'))

    def test_save_is_atomic_and_leaves_no_temporary_file(self):
        from app.models.schemas import CandidateProfile
        profile_store.save_profile(CandidateProfile(name='Test Member'))
        self.assertEqual(json.loads(profile_store.PROFILE_PATH.read_text(encoding='utf-8'))['name'], 'Test Member')
        self.assertEqual([p.name for p in self.directory.iterdir() if p.suffix == '.tmp'], [])


if __name__ == '__main__':
    unittest.main()
