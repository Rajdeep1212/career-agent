"""M2 commit 5: outreach is recorded as events, and removing a tracked job leaves a note in its log
(docs/M2_PLAN.md §1.1, §1.5, decisions Q2 and Q7). Fictional data and temporary databases only."""
import json
from unittest.mock import patch

from app.models.schemas import JobPosting
from app.storage import alert_store, career_store
from test_security_regressions import LOCAL, _IsolatedApp

SAVED = JobPosting(company='ExampleCo', title='Machine Learning Engineer', location='Bengaluru', source='Saved by you',
                   application_url='https://careers.example.org/jobs/42')


class _App(_IsolatedApp):
    def setUp(self):
        super().setUp()
        patcher = patch.object(alert_store, 'DB_PATH', self.directory / 'alerts.sqlite3')
        patcher.start()
        self.addCleanup(patcher.stop)

    def types(self, job_id):
        return [event['event_type'] for event in career_store.job_events(job_id)]


class OutreachEventTests(_App):
    def setUp(self):
        super().setUp()
        self.job_id = career_store.upsert_job({'company': 'ExampleCo', 'title': 'Data Analyst', 'location': 'Pune'})
        self.application = career_store.save_application(self.job_id, status='APPLIED')

    def test_preparing_outreach_is_one_event_per_draft(self):
        career_store.link_outreach(self.application['id'], 5, 'Hello')
        career_store.link_outreach(self.application['id'], 5, 'Hello, edited')
        self.assertEqual(self.types(self.job_id), ['applied', 'outreach_prepared'])
        event = career_store.job_events(self.job_id)[-1]
        self.assertEqual(event['application_id'], self.application['id'])
        self.assertEqual(json.loads(event['context_json']), {'draft_id': 5})
        career_store.link_outreach(self.application['id'], 6, 'Second draft')
        self.assertEqual(self.types(self.job_id).count('outreach_prepared'), 2)

    def test_sending_is_one_event_and_only_for_a_linked_draft(self):
        career_store.link_outreach(self.application['id'], 5, 'Hello')
        career_store.mark_outreach_sent(5)
        career_store.mark_outreach_sent(5)
        career_store.mark_outreach_sent(999)
        self.assertEqual(self.types(self.job_id), ['applied', 'outreach_prepared', 'outreach_sent'])
        self.assertEqual(json.loads(career_store.job_events(self.job_id)[-1]['context_json']), {'draft_id': 5})

    def test_outreach_changes_neither_the_status_nor_the_response_state(self):
        career_store.link_outreach(self.application['id'], 5, 'Hello')
        career_store.mark_outreach_sent(5)
        current = career_store.get_application(self.application['id'])
        self.assertEqual((current['status'], current['outreach_state']), ('APPLIED', 'SENT'))
        self.assertEqual(current['response_state'], 'PENDING_CENSORED', 'your own email is not a response')
        self.assertEqual(current['last_event']['event_type'], 'applied', 'Undo stays on tracker events')
        self.assertFalse(current['shortlisted'])


class RemovalNoteTests(_App):
    def saved_job(self):
        alert_store.upsert([SAVED], kind='capture')
        return career_store.upsert_job(SAVED.model_dump(mode='json'))

    def remove(self, job_id):
        return self.client.delete(f'/capture/jobs/{job_id}', headers=LOCAL)

    def test_removing_a_tracked_job_adds_a_note_and_keeps_its_history(self):
        job_id = self.saved_job()
        application = career_store.save_application(job_id)
        career_store.update_application(application['id'], status='APPLIED')
        self.assertEqual(self.remove(job_id).status_code, 200)
        self.assertEqual(self.types(job_id), ['saved', 'applied', 'removed_from_results'])
        note = career_store.job_events(job_id)[-1]
        self.assertEqual((note['application_id'], note['source']), (application['id'], 'user'))
        self.assertIn('Saved by you', note['note'])
        current = career_store.get_application(application['id'])
        self.assertEqual(current['status'], 'APPLIED', 'removal is not a withdrawal')
        self.assertIsNotNone(career_store.get_job(job_id))

    def test_removing_a_job_with_no_history_records_nothing(self):
        job_id = self.saved_job()
        self.assertEqual(self.remove(job_id).status_code, 200)
        self.assertEqual(career_store.job_events(job_id), [])
        self.assertEqual(career_store.list_applications(), [])
