"""The status set, the derived M2 response states (docs/M2_PLAN.md §1.1-1.2), and the store's applications, which are
tracker rows since TRK3c. Fictional data and temporary databases only."""
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import get_args
from unittest.mock import patch

from app.core.config import settings
from app.models.career import ApplicationStatus
from app.storage import career_events, career_store, db
from app.tracker import service, store
from test_security_regressions import LOCAL, _IsolatedApp
from tracker_helpers import isolate_tracker

NEW_STATUSES = ('SAVED', 'APPLIED', 'ONLINE_TEST', 'INTERVIEW', 'OFFER', 'REJECTED', 'WITHDRAWN', 'SKIPPED')
JOB = {'company': 'ExampleCo', 'title': 'Data Analyst', 'location': 'Pune', 'description': 'SQL and Python'}
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


class _Store(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.object(career_store, 'DB_PATH', Path(directory.name) / 'agent.sqlite3')
        patcher.start()
        self.addCleanup(patcher.stop)
        isolate_tracker(self, Path(directory.name))
        db.reset_cache()
        self.addCleanup(db.reset_cache)
        self.job_id = career_store.upsert_job(JOB)

    def funnel(self, application_id):
        return [event['event_type'] for event in career_store.get_application(application_id)['timeline']]


class StatusSetTests(unittest.TestCase):
    def test_the_status_set_is_the_new_one_everywhere(self):
        self.assertEqual(career_events.STATUSES, NEW_STATUSES)
        self.assertEqual(get_args(ApplicationStatus), NEW_STATUSES)

    def test_the_response_window_is_a_setting(self):
        self.assertEqual(settings.response_window_days, 21)


class StoreTests(_Store):
    """TRK3c: the store's applications are tracker rows; a status changes by a tracker event, never in the M2 log."""

    def test_saving_records_one_status_event_and_saving_again_records_none(self):
        application = career_store.save_application(self.job_id)
        self.assertEqual(application['status'], 'SAVED')
        self.assertEqual(career_store.save_application(self.job_id)['id'], application['id'])
        self.assertEqual(self.funnel(application['id']), ['saved'])
        self.assertEqual(application['job_id'], self.job_id)
        self.assertEqual(career_store.job_events(self.job_id), [])

    def test_a_status_change_is_an_event_and_notes_or_the_same_status_are_not(self):
        application = career_store.save_application(self.job_id)
        career_store.update_application(application['id'], notes='Call HR')
        career_store.update_application(application['id'], status='SAVED')
        career_store.update_application(application['id'], status='APPLIED')
        career_store.update_application(application['id'], status='INTERVIEW', notes='Round 1')
        self.assertEqual(self.funnel(application['id']), ['saved', 'applied', 'interview'])
        current = career_store.get_application(application['id'])
        self.assertEqual((current['status'], current['notes']), ('INTERVIEW', 'Round 1'))
        self.assertIsNotNone(current['applied_at'])

    def test_any_status_sequence_follows_the_tracker_transitions_or_is_refused(self):
        application = career_store.save_application(self.job_id)
        chooser = random.Random(20260930)
        for _ in range(40):
            before = career_store.get_application(application['id'])
            target = chooser.choice(NEW_STATUSES)
            try:
                career_store.update_application(application['id'], status=target)
            except ValueError:
                self.assertNotIn(target, (*service.TRANSITIONS[before['status']], before['status']))
                self.assertEqual(career_store.get_application(application['id'])['timeline'], before['timeline'])
                continue
            self.assertEqual(career_store.get_application(application['id'])['status'], target)
        self.assertEqual(career_store.job_events(self.job_id), [])

    def test_the_tracker_keeps_the_post_and_the_stored_job_is_left_alone(self):
        stored = career_store.get_job(self.job_id)
        application = career_store.save_application(self.job_id, status='APPLIED')
        detail = service.get_application(store.LOCAL_USER_ID, application['id'])
        self.assertEqual((detail['snapshot']['title'], detail['snapshot']['description']), (JOB['title'], JOB['description']))
        self.assertEqual(career_store.get_job(self.job_id), stored)

    def test_legacy_statuses_are_refused(self):
        application = career_store.save_application(self.job_id)
        for legacy in ('DISCOVERED', 'OUTREACH_PREPARED', 'OUTREACH_SENT'):
            with self.assertRaises(ValueError, msg=legacy):
                career_store.update_application(application['id'], status=legacy)
        self.assertEqual(self.funnel(application['id']), ['saved'])


class ResponseStateTests(unittest.TestCase):
    def events(self, *items):
        return [{'id': index + 1, 'event_type': event_type, 'undoes_event_id': undoes,
                 'occurred_at': (NOW - timedelta(days=days_ago)).isoformat()}
                for index, (event_type, days_ago, undoes) in enumerate(items)]

    def state(self, events, window=21):
        return career_events.response_state(events, now=NOW, window_days=window)

    def test_not_applied_has_no_response_state(self):
        self.assertIsNone(self.state(self.events(('saved', 40, None))))

    def test_under_the_window_is_censored_and_at_the_window_is_no_response(self):
        self.assertEqual(self.state(self.events(('applied', 5, None))), 'PENDING_CENSORED')
        self.assertEqual(self.state(self.events(('applied', 20.99, None))), 'PENDING_CENSORED')
        self.assertEqual(self.state(self.events(('applied', 21, None))), 'NO_RESPONSE')
        self.assertEqual(self.state(self.events(('applied', 21, None)), window=30), 'PENDING_CENSORED')

    def test_any_response_after_applying_ends_censoring(self):
        for response in ('recruiter_reply', 'online_test', 'interview', 'offer', 'rejected'):
            self.assertEqual(self.state(self.events(('applied', 30, None), (response, 2, None))), 'RESPONDED', response)

    def test_an_undone_response_does_not_count_and_a_confirmation_does(self):
        self.assertEqual(self.state(self.events(('applied', 30, None), ('recruiter_reply', 2, None),
                                                ('undone', 1, 2))), 'NO_RESPONSE')
        self.assertEqual(self.state(self.events(('applied', 3, None), ('no_response_confirmed', 1, None))),
                         'NO_RESPONSE')

    def test_an_undone_application_has_no_response_state(self):
        self.assertIsNone(self.state(self.events(('applied', 30, None), ('undone', 1, 1))))


class StoreStatusTests(_IsolatedApp):
    """The M2 routes are retired (TRK3b); the chat's update tool changes a status in the tracker (TRK3c)."""

    def setUp(self):
        super().setUp()
        self.job_id = career_store.upsert_job(JOB)
        self.application = career_store.save_application(self.job_id)

    def test_legacy_statuses_are_refused(self):
        for legacy in ('DISCOVERED', 'OUTREACH_PREPARED', 'OUTREACH_SENT'):
            with self.assertRaises(ValueError, msg=legacy):
                career_store.update_application(self.application['id'], legacy)

    def test_a_status_change_is_a_tracker_event_and_shows_in_the_v1_api(self):
        updated = career_store.update_application(self.application['id'], 'APPLIED')
        self.assertEqual(updated['status'], 'APPLIED')
        listed = self.client.get('/api/v1/applications', headers=LOCAL).json()['applications']
        self.assertEqual([(row['id'], row['status'], row['job_id']) for row in listed],
                         [(self.application['id'], 'APPLIED', self.job_id)])
        self.assertEqual(career_store.job_events(self.job_id), [])


if __name__ == '__main__':
    unittest.main()
