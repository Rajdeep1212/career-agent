"""Career tracking tests use fictional data and isolated databases only."""
import importlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import closing

from fastapi.testclient import TestClient

from app.models.career import ContactCandidate


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'agent.sqlite3'
        self.assertIsNotNone(importlib.util.find_spec('app.storage.career_store'), 'career store must exist')
        previous_config = sys.modules.get('app.core.config')
        sys.modules['app.core.config'] = SimpleNamespace(settings=SimpleNamespace(data_dir=self.temp.name))
        try:
            self.store = importlib.import_module('app.storage.career_store')
        finally:
            if previous_config is None:
                sys.modules.pop('app.core.config', None)
            else:
                sys.modules['app.core.config'] = previous_config
        self.db_patch = patch.object(self.store, 'DB_PATH', self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.job = {'company': 'ExampleCo', 'title': 'Data Analyst', 'location': 'Oslo',
                    'application_url': 'https://example.org/jobs/123?utm_source=test',
                    'match_result': {'overall_score': 81, 'explanation': 'SQL evidence'}}

    def save_job(self):
        return self.store.upsert_job(self.job)

    def test_job_identity_stable_and_extra_data_preserved(self):
        identity = self.save_job()
        revised = dict(self.job, application_url='https://example.org/jobs/123', match_result={'overall_score': 85})
        self.assertEqual(self.store.upsert_job(revised), identity)
        self.assertEqual(self.store.get_job(identity)['match_result'], {'overall_score': 85})
        self.assertIsNone(self.store.get_job('absent'))

    def test_sessions_replace_response_preserve_id(self):
        identity = self.store.save_session(None, {'locations': ['Oslo']}, {'jobs': []})
        self.assertEqual(self.store.save_session(identity, {'minimum_match_score': 75}, {'jobs': [self.job]}), identity)
        self.assertEqual(self.store.get_session(identity)['intent'], {'minimum_match_score': 75})
        self.assertEqual(self.store.get_session(identity)['response']['jobs'], [self.job])
        self.assertIsNone(self.store.get_session('absent'))

    def test_idempotent_save_keeps_status_notes(self):
        job_id = self.save_job()
        first = self.store.save_application(job_id, notes='Review tomorrow')
        applied = self.store.update_application(first['id'], status='APPLIED', notes='Submitted myself')
        again = self.store.save_application(job_id)
        self.assertEqual(again['id'], first['id'])
        self.assertEqual(again['status'], 'APPLIED')
        self.assertEqual(again['notes'], 'Submitted myself')
        self.assertEqual(again['applied_at'], applied['applied_at'])
        self.assertEqual(len(self.store.list_applications()), 1)

    def test_applied_timestamp_only_explicit_transition(self):
        application = self.store.save_application(self.save_job())
        self.assertIsNone(application['applied_at'])
        self.store.get_job(application['job_id'])
        self.store.link_outreach(application['id'], 101, 'Hello from candidate')
        self.assertIsNone(self.store.get_application(application['id'])['applied_at'])
        applied = self.store.update_application(application['id'], status='APPLIED')
        self.assertIsNotNone(applied['applied_at'])
        self.assertEqual(self.store.update_application(application['id'], notes='Added note')['applied_at'], applied['applied_at'])

    def test_outreach_preserves_interview_status_and_history(self):
        application = self.store.save_application(self.save_job(), status='INTERVIEW')
        self.store.link_outreach(application['id'], 102, 'First message')
        self.store.mark_outreach_sent(102)
        current = self.store.get_application(application['id'])
        self.assertEqual(current['status'], 'INTERVIEW')
        self.assertEqual(current['outreach_state'], 'SENT')
        self.assertEqual(current['outreach'][0]['draft_id'], 102)
        self.assertIsNotNone(current['outreach'][0]['sent_at'])
        self.store.link_outreach(application['id'], 102, 'First message')
        self.assertEqual(self.store.get_application(application['id'])['outreach_state'], 'SENT')

    def test_invalid_status_and_missing_job_rejected(self):
        with self.assertRaises(ValueError):
            self.store.save_application('unknown')
        with self.assertRaises(ValueError):
            self.store.save_application(self.save_job(), status='OPENED')
        self.assertIsNone(self.store.update_application('missing', notes='Hello'))

    def test_additive_schema_preserves_existing_tables(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('CREATE TABLE email_drafts (id INTEGER PRIMARY KEY, body TEXT)')
            conn.execute("INSERT INTO email_drafts VALUES(1, 'Keep draft')")
            conn.execute('CREATE TABLE oauth_tokens (provider TEXT, encrypted TEXT)')
            conn.execute("INSERT INTO oauth_tokens VALUES('sample', 'keep ciphertext')")
        self.save_job()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertEqual(conn.execute('SELECT body FROM email_drafts').fetchone()[0], 'Keep draft')
            self.assertEqual(conn.execute('SELECT encrypted FROM oauth_tokens').fetchone()[0], 'keep ciphertext')
            self.assertGreater(conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0], 0)

    def test_contact_link_migration_preserves_existing_outreach(self):
        stamp = '2026-01-01T00:00:00+00:00'
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executescript('''
                CREATE TABLE career_jobs (
                    id TEXT PRIMARY KEY, job_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE career_applications (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES career_jobs(id),
                    status TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, applied_at TEXT,
                    outreach_state TEXT NOT NULL DEFAULT 'NONE'
                );
                CREATE TABLE career_outreach (
                    draft_id INTEGER PRIMARY KEY,
                    application_id TEXT NOT NULL REFERENCES career_applications(id),
                    short_message TEXT NOT NULL, created_at TEXT NOT NULL, sent_at TEXT
                );
            ''')
            conn.execute('INSERT INTO career_jobs VALUES (?, ?, ?, ?)', (
                'legacy-job', '{"company":"ExampleCo","title":"Analyst"}', stamp, stamp,
            ))
            conn.execute('INSERT INTO career_applications VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (
                'legacy-application', 'legacy-job', 'SAVED', '', stamp, stamp, None, 'PREPARED',
            ))
            conn.execute('INSERT INTO career_outreach VALUES (?, ?, ?, ?, ?)', (
                41, 'legacy-application', 'Existing message', stamp, None,
            ))

        application = self.store.get_application('legacy-application')

        self.assertEqual(application['outreach'][0]['short_message'], 'Existing message')
        self.assertIsNone(application['outreach'][0]['contact_id'])
        with closing(sqlite3.connect(self.db)) as conn:
            columns = [row[1] for row in conn.execute('PRAGMA table_info(career_outreach)')]
        self.assertIn('contact_id', columns)

    def test_contact_roundtrip(self):
        contact = self.store.save_contact(ContactCandidate(company='ExampleCo', name='Fictional Recruiter', contact_method='recruiter@example.org', public_source='Provided by user'))
        self.assertEqual(contact['confidence'], 'user_provided')
        self.assertEqual(self.store.list_contacts('exampleco')[0]['id'], contact['id'])

    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self.assertIsNotNone(importlib.util.find_spec('app.api.career'), 'career API must exist')
        router = importlib.import_module('app.api.career').router
        app = FastAPI()
        app.include_router(router)
        return TestClient(
            app,
            base_url='http://localhost:8010',
            headers={'Origin': 'http://localhost:8010'},
        )

    def test_api_application_lifecycle(self):
        job_id = self.save_job()
        with self.client() as client:
            created = client.post('/applications', json={'job_id': job_id, 'notes': 'Saved via API'})
            self.assertIn(created.status_code, (200, 201))
            identity = created.json()['id']
            updated = client.patch('/applications/' + identity, json={'status': 'APPLIED'})
            self.assertEqual(updated.status_code, 200)
            self.assertIsNotNone(updated.json()['applied_at'])
            self.assertEqual(len(client.get('/applications').json()), 1)
            self.assertEqual(client.get('/career/jobs/' + job_id).json()['company'], 'ExampleCo')
            self.assertEqual(client.get('/career/jobs/absent').status_code, 404)
            self.assertEqual(client.patch('/applications/absent', json={'notes': 'x'}).status_code, 404)
            self.assertEqual(client.patch('/applications/' + identity, json={'status': 'OPENED'}).status_code, 422)

    def test_api_sessions(self):
        identity = self.store.save_session(None, {'roles_requested': ['Data Analyst']}, {'jobs': []})
        with self.client() as client:
            self.assertEqual(client.get('/agent/sessions/' + identity).json()['id'], identity)
            self.assertEqual(client.get('/agent/sessions/absent').status_code, 404)

    def test_contact_api_never_claims_verification_or_accepts_header_injection(self):
        data = {'company': 'ExampleCo', 'contact_method': 'recruiter@example.org', 'public_source': 'Provided by user', 'confidence': 'public_verified'}
        with self.client() as client:
            saved = client.post('/contacts', json=data)
            self.assertIn(saved.status_code, (200, 201))
            self.assertEqual(saved.json()['confidence'], 'user_provided')
            self.assertEqual(len(client.get('/contacts', params={'company': 'ExampleCo'}).json()), 1)
            for bad in ['person@example.org\r\nBcc: other@example.org', 'not an email', 'javascript:alert(1)', 'https://user:pass@example.org/profile']:
                self.assertEqual(client.post('/contacts', json=dict(data, contact_method=bad)).status_code, 422)


class MainTrackerAPITests(unittest.TestCase):
    origin = {'Origin': 'http://localhost:8010'}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'agent.sqlite3'
        from app import main
        from app.storage import career_store
        self.main = main
        self.store = career_store
        self.db_patch = patch.object(career_store, 'DB_PATH', self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.client = TestClient(main.app, base_url='http://localhost:8010', raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.job = {
            'company': 'RuntimeCo',
            'title': 'Runtime Analyst',
            'location': 'Remote',
            'application_url': 'https://jobs.example.org/runtime',
            'skills': ['SQL'],
        }

    def save_job(self, **extra):
        return self.store.upsert_job({**self.job, **extra})

    def save_via_api(self, job_id, **extra):
        return self.client.post(
            '/applications',
            json={'job_id': job_id, 'status': 'SAVED', 'notes': '', **extra},
            headers=self.origin,
        )

    def test_real_app_router_is_reachable_and_save_is_idempotent(self):
        job_id = self.save_job()
        self.assertEqual(self.client.get('/applications').status_code, 200)

        first = self.save_via_api(job_id, notes='Review tomorrow')
        second = self.save_via_api(job_id, notes='Must not replace existing notes')

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()['id'], first.json()['id'])
        self.assertEqual(second.json()['notes'], 'Review tomorrow')
        fetched = self.client.get('/applications/' + first.json()['id'])
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()['job_id'], job_id)
        self.assertEqual(len(self.client.get('/applications').json()), 1)

    def test_tracker_mutations_require_exact_local_origin(self):
        job_id = self.save_job()
        payload = {'job_id': job_id, 'status': 'SAVED', 'notes': ''}
        self.assertEqual(self.client.post('/applications', json=payload).status_code, 403)
        self.assertEqual(self.client.post(
            '/applications', json=payload, headers={'Origin': 'https://attacker.example'},
        ).status_code, 403)
        created = self.client.post('/applications', json=payload, headers=self.origin)
        self.assertEqual(created.status_code, 200)

        application_id = created.json()['id']
        self.assertEqual(self.client.patch(
            f'/applications/{application_id}', json={'notes': 'missing origin'},
        ).status_code, 403)
        self.assertEqual(self.client.patch(
            f'/applications/{application_id}',
            json={'notes': 'cross site'},
            headers={'Origin': 'https://attacker.example'},
        ).status_code, 403)
        self.assertEqual(self.client.patch(
            f'/applications/{application_id}', json={'notes': 'local update'}, headers=self.origin,
        ).status_code, 200)

        contact = {
            'company': 'RuntimeCo', 'contact_method': 'person@example.org',
            'public_source': 'Provided by user',
        }
        self.assertEqual(self.client.post('/contacts', json=contact).status_code, 403)
        self.assertEqual(self.client.post('/contacts', json=contact, headers=self.origin).status_code, 200)

    def test_status_and_notes_persist_across_supported_lifecycle(self):
        created = self.save_via_api(self.save_job()).json()
        statuses = [
            'DISCOVERED', 'SAVED', 'APPLIED', 'OUTREACH_PREPARED', 'OUTREACH_SENT',
            'INTERVIEW', 'REJECTED', 'OFFER', 'SKIPPED',
        ]
        for index, status in enumerate(statuses):
            response = self.client.patch(
                f"/applications/{created['id']}",
                json={'status': status, 'notes': f'note {index}'},
                headers=self.origin,
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['status'], status)
            self.assertEqual(response.json()['notes'], f'note {index}')

        fetched = self.client.get(f"/applications/{created['id']}").json()
        self.assertEqual(fetched['status'], 'SKIPPED')
        self.assertEqual(fetched['notes'], 'note 8')
        self.assertIsNotNone(fetched['applied_at'])

    def test_invalid_ids_and_states_fail_safely(self):
        self.assertEqual(self.client.get('/applications/missing').status_code, 404)
        missing_update = self.client.patch(
            '/applications/missing', json={'notes': 'x'}, headers=self.origin,
        )
        self.assertEqual(missing_update.status_code, 404)
        invalid_state = self.client.patch(
            '/applications/missing', json={'status': 'OPENED'}, headers=self.origin,
        )
        self.assertEqual(invalid_state.status_code, 422)
        missing_job = self.save_via_api('missing')
        self.assertEqual(missing_job.status_code, 404)
        for response in (missing_update, invalid_state, missing_job):
            self.assertNotIn('sqlite', response.text.lower())
            self.assertNotIn(str(self.db).lower(), response.text.lower())

    def test_viewing_job_and_application_never_marks_applied(self):
        job_id = self.save_job()
        viewed_job = self.client.get('/career/jobs/' + job_id)
        self.assertEqual(viewed_job.status_code, 200)
        self.assertEqual(self.client.get('/applications').json(), [])

        created = self.save_via_api(job_id).json()
        self.assertEqual(self.client.get('/career/jobs/' + job_id).status_code, 200)
        viewed_application = self.client.get('/applications/' + created['id']).json()
        self.assertEqual(viewed_application['status'], 'SAVED')
        self.assertIsNone(viewed_application['applied_at'])

    def test_outreach_state_remains_linked_without_changing_application_status(self):
        application = self.store.save_application(self.save_job(), status='INTERVIEW')
        self.store.link_outreach(application['id'], 501, 'Prepared message')

        prepared = self.client.get('/applications/' + application['id']).json()
        self.assertEqual(prepared['status'], 'INTERVIEW')
        self.assertEqual(prepared['outreach_state'], 'PREPARED')
        self.assertEqual(prepared['outreach'][0]['draft_id'], 501)

        self.store.mark_outreach_sent(501)
        sent = self.client.get('/applications/' + application['id']).json()
        self.assertEqual(sent['status'], 'INTERVIEW')
        self.assertEqual(sent['outreach_state'], 'SENT')
        self.assertIsNotNone(sent['outreach'][0]['sent_at'])

    def test_job_application_and_session_responses_remove_internal_fields(self):
        private = 'private-token-value'
        job_id = self.save_job(
            provider='JSearch',
            _cache_key='internal-cache',
            raw_provider_payload={'access_token': private},
            refresh_token=private,
        )
        application = self.store.save_application(job_id)
        session_id = self.store.save_session(
            None,
            {'roles_requested': ['Analyst'], '_private': private, 'authorization_code': private},
            {
                'results': [{'id': job_id, 'company': 'RuntimeCo'}],
                'provider': 'JSearch',
                '_ranked_candidates': [{'raw_provider_payload': private}],
                '_profile_key': private,
                'nested': {'client_secret': private, 'visible': 'keep'},
            },
        )

        job = self.client.get('/career/jobs/' + job_id)
        tracked = self.client.get('/applications/' + application['id'])
        session = self.client.get('/agent/sessions/' + session_id)

        for response in (job, tracked, session):
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn(private, response.text)
            self.assertNotIn('raw_provider_payload', response.text)
            self.assertNotIn('refresh_token', response.text)
        self.assertEqual(session.json()['response']['provider'], 'JSearch')
        self.assertEqual(session.json()['response']['nested'], {'visible': 'keep'})
        self.assertNotIn('_ranked_candidates', session.json()['response'])
        self.assertNotIn('_profile_key', session.json()['response'])


if __name__ == '__main__':
    unittest.main()
