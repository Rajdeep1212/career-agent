"""Outreach is recorded as events (in the tracker since TRK3c), and removing a tracked job leaves a note in the M2
log (docs/M2_PLAN.md §1.1, §1.5, decisions Q2 and Q7). Fictional data and temporary databases only."""
import json
from unittest.mock import patch

from app.models.schemas import JobPosting
from app.storage import alert_store, career_store
from app.tracker import service, store
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

    def timeline(self, application_id):
        return career_store.get_application(application_id)['timeline']

    def tracker_types(self, application_id):
        return [event['event_type'] for event in self.timeline(application_id)]


class OutreachEventTests(_App):
    """TRK3c: outreach events are in the tracker log (data/tracker.sqlite3); the M2 log gets none."""

    def setUp(self):
        super().setUp()
        self.job_id = career_store.upsert_job({'company': 'ExampleCo', 'title': 'Data Analyst', 'location': 'Pune'})
        self.application = career_store.save_application(self.job_id, status='APPLIED')

    def test_preparing_outreach_is_one_event_per_draft(self):
        career_store.link_outreach(self.application['id'], 5, 'Hello')
        career_store.link_outreach(self.application['id'], 5, 'Hello, edited')
        self.assertEqual(self.tracker_types(self.application['id']), ['applied', 'outreach_prepared'])
        event = self.timeline(self.application['id'])[-1]
        self.assertEqual((event['source'], event['request_id']), ('derived', 'outreach_prepared:draft:5'))
        career_store.link_outreach(self.application['id'], 6, 'Second draft')
        self.assertEqual(self.tracker_types(self.application['id']).count('outreach_prepared'), 2)
        self.assertEqual(self.types(self.job_id), [], 'nothing goes to the M2 log')

    def test_sending_is_one_event_and_only_for_a_linked_draft(self):
        career_store.link_outreach(self.application['id'], 5, 'Hello')
        career_store.mark_outreach_sent(5)
        career_store.mark_outreach_sent(5)
        career_store.mark_outreach_sent(999)
        self.assertEqual(self.tracker_types(self.application['id']), ['applied', 'outreach_prepared', 'outreach_sent'])
        self.assertEqual(self.timeline(self.application['id'])[-1]['request_id'], 'outreach_sent:draft:5')
        self.assertEqual(self.types(self.job_id), [])

    def test_outreach_changes_neither_the_status_nor_what_undo_takes_back(self):
        career_store.link_outreach(self.application['id'], 5, 'Hello')
        career_store.mark_outreach_sent(5)
        current = career_store.get_application(self.application['id'])
        self.assertEqual((current['status'], current['outreach_state']), ('APPLIED', 'SENT'))
        [listed] = service.list_applications(store.LOCAL_USER_ID)
        self.assertIsNone(listed['latest_event'], 'Undo stays on status events; only the first one is left')
        sent = self.timeline(self.application['id'])[-1]
        with self.assertRaises(service.Conflict):
            service.undo_event(store.LOCAL_USER_ID, self.application['id'], sent['id'], 'undo-1')

    def test_the_api_does_not_take_outreach_events(self):
        response = self.client.post(f"/api/v1/applications/{self.application['id']}/events", headers={**LOCAL, 'Idempotency-Key': 'k-1'},
                                    json={'event_type': 'outreach_sent'})
        self.assertEqual(response.status_code, 422)


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
        self.assertEqual(self.types(job_id), ['removed_from_results'])
        note = career_store.job_events(job_id)[-1]
        self.assertEqual(note['source'], 'user')
        self.assertEqual(json.loads(note['context_json']), {'tracker_application_id': application['id']})
        self.assertIn('Saved by you', note['note'])
        current = career_store.get_application(application['id'])
        self.assertEqual(current['status'], 'APPLIED', 'removal is not a withdrawal')
        self.assertEqual(self.tracker_types(application['id']), ['saved', 'applied'])
        self.assertIsNotNone(career_store.get_job(job_id))

    def test_removing_a_job_with_no_history_records_nothing(self):
        job_id = self.saved_job()
        self.assertEqual(self.remove(job_id).status_code, 200)
        self.assertEqual(career_store.job_events(job_id), [])
        self.assertEqual(career_store.list_applications(), [])
