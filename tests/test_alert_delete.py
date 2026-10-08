"""Removing a saved or alert job: kept as a recoverable copy, not brought back by later alert emails."""
import gc
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.models.schemas import CandidateProfile, JobPosting
from app.providers.alerts_provider import AlertsProvider
from app.services import application_verifier
from app.services.career_agent import CareerAgent
from app.sources.alerts.ingest import ingest_raw
from app.storage import alert_store, career_store, db, history, preference_store, profile_store
from tracker_helpers import isolate_tracker

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "alerts"
LOCAL = {'Origin': 'http://localhost:8010'}


def saved_job(title='Machine Learning Engineer', company='Naukri Test'):
    return JobPosting(company=company, title=title, location='Bengaluru', source='Saved by you',
                      application_url='https://careers.example.org/jobs/42')


class _Isolated(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(directory.name)
        for module, name, value in ((history, 'DB_PATH', self.root / 'history.sqlite3'),
                                    (career_store, 'DB_PATH', self.root / 'agent.sqlite3'),
                                    (profile_store, 'PROFILE_PATH', self.root / 'profile.json'),
                                    (preference_store, 'PREFERENCES_PATH', self.root / 'preferences.json'),
                                    (alert_store, 'DB_PATH', self.root / 'alerts.sqlite3')):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        isolate_tracker(self, self.root)
        patcher = patch.object(application_verifier, 'safe_get', AsyncMock(side_effect=OSError('offline')))
        patcher.start()
        self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)
        profile_store.save_profile(CandidateProfile(graduation_year=2025, experience_years=0, skills=['Python'],
                                                    preferred_roles=['Machine Learning Engineer'], preferred_locations=['Bengaluru']))


class StoreTests(_Isolated):
    def test_delete_keeps_a_recoverable_copy(self):
        alert_store.upsert([saved_job()], kind='capture')
        self.assertTrue(alert_store.delete(saved_job()))
        self.assertEqual(alert_store.list_jobs(), [])
        with sqlite3.connect(alert_store.DB_PATH) as conn:
            copies = conn.execute('SELECT job_json FROM deleted_alert_jobs').fetchall()
        self.assertEqual(len(copies), 1)
        self.assertIn('Naukri Test', copies[0][0])
        self.assertFalse(alert_store.delete(saved_job()))

    def test_a_later_alert_email_does_not_bring_it_back(self):
        ingest_raw([('dropbox', (FIXTURES / 'linkedin_alert.eml').read_bytes())])
        target = next(job for job in alert_store.list_jobs() if job.title == 'Machine Learning Engineer')
        self.assertTrue(alert_store.delete(target))
        again = (FIXTURES / 'linkedin_alert.eml').read_bytes().replace(b'<synthetic-linkedin-0001@example.com>', b'<later@example.com>')
        ingest_raw([('dropbox', again)])
        self.assertNotIn('Machine Learning Engineer', [job.title for job in alert_store.list_jobs()])

    def test_saving_it_again_with_the_bookmarklet_restores_it(self):
        alert_store.upsert([saved_job()], kind='capture')
        alert_store.delete(saved_job())
        alert_store.upsert([saved_job()], kind='capture')
        self.assertEqual([job.company for job in alert_store.list_jobs()], ['Naukri Test'])

    def test_an_existing_database_is_backed_up_and_keeps_its_jobs(self):
        with db.connect(alert_store.DB_PATH) as conn:   # a database made before this change: v1 only
            conn.execute('CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
            alert_store.MIGRATIONS[0].apply(conn)
            conn.execute("INSERT INTO schema_migrations VALUES ('alerts_v1', 'then')")
        db.reset_cache()
        alert_store.upsert([saved_job()], kind='capture')
        db.reset_cache()
        alert_store.list_jobs()
        self.assertEqual(len(alert_store.list_jobs()), 1)
        self.assertTrue(list((self.root / 'backups').glob('*/alerts.sqlite3')))
        self.assertTrue(alert_store.delete(saved_job()))


class DeleteApiTests(_Isolated):
    async def _search_ids(self):
        response = await CareerAgent([AlertsProvider()]).search('machine learning engineer', include_seen=True)
        return {result['title'] + ' @ ' + result['company']: result['id'] for result in response['results']}

    async def test_remove_from_the_dashboard(self):
        from app.main import app
        alert_store.upsert([saved_job()], kind='capture')
        ids = await self._search_ids()
        job_id = ids['Machine Learning Engineer @ Naukri Test']
        client = TestClient(app, base_url='http://localhost:8010', raise_server_exceptions=False)
        self.addCleanup(client.close)
        self.assertEqual(client.delete(f'/capture/jobs/{job_id}').status_code, 403)
        self.assertEqual(client.delete(f'/capture/jobs/{job_id}', headers={'Origin': 'https://attacker.example'}).status_code, 403)
        self.assertEqual(client.delete('/capture/jobs/' + 'f' * 64, headers=LOCAL).status_code, 404)
        self.assertEqual(client.delete(f'/capture/jobs/{job_id}', headers=LOCAL).status_code, 200)
        self.assertNotIn('Machine Learning Engineer @ Naukri Test', await self._search_ids())
        self.assertEqual(client.delete(f'/capture/jobs/{job_id}', headers=LOCAL).status_code, 404)

    async def test_only_saved_and_alert_jobs_can_be_removed(self):
        from app.main import app
        official = JobPosting(company='Example', title='Data Analyst', location='Pune', source='Company Radar',
                              application_url='https://job-boards.greenhouse.io/example/jobs/1')
        result = {**official.model_dump(mode='json'), 'id': 'x'}
        job_id = career_store.upsert_job(result)
        client = TestClient(app, base_url='http://localhost:8010', raise_server_exceptions=False)
        self.addCleanup(client.close)
        self.assertEqual(client.delete(f'/capture/jobs/{job_id}', headers=LOCAL).status_code, 404)


if __name__ == '__main__':
    unittest.main()
