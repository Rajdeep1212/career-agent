"""Every job provider is optional: a missing key skips it, never an error."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.providers import registry

KEYS = {'rapidapi_key': 'rapid-secret', 'adzuna_app_id': 'adzuna-id', 'adzuna_app_key': 'adzuna-secret',
        'jooble_api_key': 'jooble-secret'}
NO_KEYS = {name: '' for name in KEYS}


def _settings(**values):
    return patch.multiple(settings, **{**NO_KEYS, 'job_providers': 'jsearch,adzuna,jooble', **values})


class ProviderRegistryTests(unittest.TestCase):
    def test_default_list_includes_all_three_providers(self):
        from app.core.config import Settings
        self.assertEqual(Settings(_env_file=None).job_providers, 'jsearch,adzuna,jooble')

    def test_missing_keys_skip_providers(self):
        with _settings():
            self.assertEqual(registry.get_providers(), [])
        with _settings(adzuna_app_id='id', adzuna_app_key='key'):
            self.assertEqual([p.name for p in registry.get_providers()], ['Adzuna'])
        with _settings(adzuna_app_id='id'):  # both Adzuna values are required
            self.assertEqual(registry.get_providers(), [])

    def test_all_configured_providers_are_used_in_listed_order(self):
        with _settings(**KEYS):
            self.assertEqual([p.name for p in registry.get_providers()], ['JSearch/RapidAPI', 'Adzuna', 'Jooble'])
        with _settings(**KEYS, job_providers='jooble, jsearch, unknown'):
            self.assertEqual([p.name for p in registry.get_providers()], ['Jooble', 'JSearch/RapidAPI'])

    def test_connection_status_lists_providers_without_secrets(self):
        from app.main import app
        with _settings(rapidapi_key='rapid-secret', jooble_api_key='jooble-secret'), \
             TestClient(app, base_url='http://localhost:8010') as client:
            response = client.get('/connections/search/status')
        body = response.json()
        self.assertTrue(body['configured'])
        self.assertEqual({p['id']: p['configured'] for p in body['providers'] if p['installed']},
                         {'jsearch': True, 'adzuna': False, 'jooble': True})
        for secret in KEYS.values():
            self.assertNotIn(secret, response.text)


if __name__ == '__main__':
    unittest.main()
