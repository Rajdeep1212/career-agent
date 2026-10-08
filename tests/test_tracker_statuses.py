"""M2 commit 2: statuses are a cache of the job event log; response states are derived, never stored
(docs/M2_PLAN.md §1.1-1.2). Fictional data and temporary databases only."""
import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import get_args
from unittest.mock import patch

from app.core.config import settings
from app.models.career import ApplicationStatus
from app.services.job_snapshot import snapshot_inputs
from app.storage import career_events, career_store, db
from test_security_regressions import _IsolatedApp

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
        db.reset_cache()
        self.addCleanup(db.reset_cache)
        self.job_id = career_store.upsert_job(JOB)

    def funnel(self):
        return [event['event_type'] for event in career_store.job_events(self.job_id)
                if event['event_type'] in career_events.FUNNEL_EVENTS]


class StatusSetTests(unittest.TestCase):
    def test_the_status_set_is_the_new_one_everywhere(self):
        self.assertEqual(career_events.STATUSES, NEW_STATUSES)
        self.assertEqual(get_args(ApplicationStatus), NEW_STATUSES)

    def test_the_response_window_is_a_setting(self):
        self.assertEqual(settings.response_window_days, 21)


class StoreTests(_Store):
    def test_saving_records_one_funnel_event_and_saving_again_records_none(self):
        application = career_store.save_application(self.job_id)
        self.assertEqual(application['status'], 'SAVED')
        career_store.save_application(self.job_id)
        self.assertEqual(self.funnel(), ['saved'])
        self.assertEqual(career_store.job_events(self.job_id)[0]['application_id'], application['id'])

    def test_a_status_change_is_an_event_and_notes_or_the_same_status_are_not(self):
        application = career_store.save_application(self.job_id)
        career_store.update_application(application['id'], notes='Call HR')
        career_store.update_application(application['id'], status='SAVED')
        career_store.update_application(application['id'], status='APPLIED')
        career_store.update_application(application['id'], status='INTERVIEW', notes='Round 1')
        self.assertEqual(self.funnel(), ['saved', 'applied', 'interview'])
        current = career_store.get_application(application['id'])
        self.assertEqual((current['status'], current['notes']), ('INTERVIEW', 'Round 1'))

    def test_the_status_cache_always_equals_the_fold_of_the_log(self):
        application = career_store.save_application(self.job_id)
        chooser = random.Random(20260930)
        for _ in range(40):
            career_store.update_application(application['id'], status=chooser.choice(NEW_STATUSES))
            events = career_store.job_events(self.job_id)
            current = career_store.get_application(application['id'])
            self.assertEqual(current['status'], career_events.status_from_events(events))
            applied = [event['occurred_at'] for event in events if event['event_type'] == 'applied']
            self.assertEqual(current['applied_at'], applied[0] if applied else None)

    def test_a_status_event_carries_the_snapshot_and_leaves_the_stored_job_alone(self):
        application = career_store.save_application(self.job_id)
        stored = career_store.get_job(self.job_id)
        career_store.update_application(application['id'], status='APPLIED',
                                        snapshot=({'name': 'Test Student'}, {'overall_score': 64}))
        applied = [event for event in career_store.job_events(self.job_id) if event['event_type'] == 'applied'][0]
        self.assertIsNotNone(applied['snapshot_id'])
        self.assertEqual(career_store.get_job(self.job_id), stored)

    def test_legacy_statuses_are_refused(self):
        application = career_store.save_application(self.job_id)
        for legacy in ('DISCOVERED', 'OUTREACH_PREPARED', 'OUTREACH_SENT'):
            with self.assertRaises(ValueError, msg=legacy):
                career_store.update_application(application['id'], status=legacy)
        self.assertEqual(self.funnel(), ['saved'])

    def test_applications_report_their_response_state(self):
        application = career_store.save_application(self.job_id)
        self.assertIsNone(career_store.get_application(application['id'])['response_state'])
        career_store.update_application(application['id'], status='APPLIED')
        self.assertEqual(career_store.get_application(application['id'])['response_state'], 'PENDING_CENSORED')


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
    """The M2 routes are retired (TRK3b); the chat's update tool still changes a status through the store."""

    def setUp(self):
        super().setUp()
        self.job_id = career_store.upsert_job(JOB)
        self.application = career_store.save_application(self.job_id)

    def test_legacy_statuses_are_refused(self):
        for legacy in ('DISCOVERED', 'OUTREACH_PREPARED', 'OUTREACH_SENT'):
            with self.assertRaises(ValueError, msg=legacy):
                career_store.update_application(self.application['id'], legacy)

    def test_a_status_change_is_logged_with_an_l0_snapshot(self):
        stored = career_store.get_job(self.job_id)
        updated = career_store.update_application(self.application['id'], 'APPLIED', snapshot=snapshot_inputs(stored))
        self.assertEqual(updated['response_state'], 'PENDING_CENSORED')
        applied = [event for event in career_store.job_events(self.job_id) if event['event_type'] == 'applied'][0]
        self.assertEqual(applied['source'], 'user')
        with career_store._connection() as conn:
            snapshot = dict(conn.execute('SELECT * FROM job_snapshots WHERE id=?', (applied['snapshot_id'],)).fetchone())
        features = json.loads(snapshot['features_json'])
        self.assertEqual(features['claim_level'], 'L0')
        self.assertIn('overall_score', features)
        self.assertIn('eligibility_status', features)
        self.assertIsNotNone(snapshot['cv_version'])
        self.assertEqual(career_store.get_job(self.job_id), stored, 'snapshotting never rewrites the stored job')


if __name__ == '__main__':
    unittest.main()
