"""The dashboard asks before a search that will spend many provider requests."""
import gc
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.schemas import JobSearchPreferences
from app.providers import jsearch_provider
from app.storage import career_store, history, preference_store, profile_store

LOCAL = {'Origin': 'http://localhost:8010'}


class SearchPreviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(gc.collect)
        self.directory = Path(temporary.name)
        for module, name, value in (
            (history, 'DB_PATH', self.directory / 'history.sqlite3'),
            (career_store, 'DB_PATH', self.directory / 'career.sqlite3'),
            (profile_store, 'PROFILE_PATH', self.directory / 'profile.json'),
            (preference_store, 'PREFERENCES_PATH', self.directory / 'preferences.json'),
            (jsearch_provider, '_last_quota', None),
            (settings, 'rapidapi_key', 'test-key'),
        ):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        no_calls = patch('app.providers.jsearch_provider.JSearchProvider.search',
                         side_effect=AssertionError('preview must not call providers'))
        no_calls.start()
        self.addCleanup(no_calls.stop)
        self.client = TestClient(app, base_url='http://localhost:8010', raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def _preview(self, message, headers=LOCAL):
        return self.client.post('/agent/search/preview', json={'message': message}, headers=headers)

    def test_default_plan_needs_no_warning(self):
        body = self._preview('Find python developer jobs').json()
        self.assertTrue(body['will_search'])
        self.assertEqual(body['provider_requests'], 4)
        self.assertEqual(len(body['queries']), 4)
        self.assertIsNone(body['warning'])

    def test_many_planned_requests_are_warned_about(self):
        preference_store.save_preferences(JobSearchPreferences(search_query_limit=8))
        body = self._preview('Find python developer jobs in Pune').json()
        self.assertEqual(body['provider_requests'], 8)
        self.assertIn('8 requests', body['warning'])
        self.assertIn('JSearch/RapidAPI', body['warning'])

    def test_low_remaining_quota_is_warned_about(self):
        reset = (datetime.now(timezone.utc) + timedelta(hours=5)).replace(microsecond=0)
        jsearch_provider._last_quota = {'remaining': 2, 'limit': 200, 'reset_at': reset.isoformat(),
                                        'observed_at': datetime.now(timezone.utc).isoformat()}
        body = self._preview('Find python developer jobs').json()
        self.assertIn('2 of 200', body['warning'])
        self.assertIn(f'{reset:%d %b %Y %H:%M} UTC', body['warning'])
        self.assertEqual(body['quotas']['JSearch/RapidAPI']['remaining'], 2)

    def test_non_search_messages_are_not_previewed(self):
        body = self._preview('How do I become a data analyst?').json()
        self.assertEqual(body, {'will_search': False})

    def test_preview_has_no_side_effects(self):
        self._preview('Find python developer jobs')
        self.assertFalse((self.directory / 'career.sqlite3').exists())
        self.assertFalse((self.directory / 'history.sqlite3').exists())

    def test_preview_requires_local_origin(self):
        self.assertEqual(self._preview('Find python developer jobs', headers={}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
