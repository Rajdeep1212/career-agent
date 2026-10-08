"""TRK3c: career_applications and the application and outreach events of job_events are read-only history. Outreach,
the chat's save/update tools and the send flow write the tracker; only thumbs and removal notes still go to job_events.
Fictional data and temporary databases only; no mail is sent."""
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from app import main
from app.agent.tools import CareerGraphTools
from app.models.schemas import JobPosting
from app.storage import alert_store, career_events, career_store, email_store
from app.tracker import service, store
from test_security_regressions import LOCAL, _IsolatedApp

ROOT = Path(__file__).resolve().parents[1]
STAMP = '2026-09-01T10:00:00+00:00'
JOB = {'company': 'ExampleCo', 'title': 'Data Analyst', 'location': 'Pune', 'skills': ['SQL'],
       'description': 'SQL reporting.', 'application_url': 'https://careers.example.org/jobs/7', 'source': 'Saved by you'}


class OldTablesReadOnlyTests(_IsolatedApp):
    def setUp(self):
        super().setUp()
        for module, name in ((email_store, 'DB_PATH'), (career_store, 'DB_PATH')):
            patcher = patch.object(module, name, self.directory / 'agent.sqlite3')
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(alert_store, 'DB_PATH', self.directory / 'alerts.sqlite3')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.legacy_job = career_store.upsert_job({**JOB, 'title': 'Legacy Analyst', 'application_url': 'https://careers.example.org/jobs/1'})
        with career_store._connection() as conn:     # M2 history, as the retired M2 tracker left it
            conn.execute("INSERT INTO career_applications (id, job_id, status, notes, created_at, updated_at, outreach_state) "
                         "VALUES ('m2-app', ?, 'SAVED', 'old note', ?, ?, 'PREPARED')", (self.legacy_job, STAMP, STAMP))
            conn.execute("INSERT INTO job_events (job_id, application_id, event_type, occurred_at, recorded_at, source) "
                         "VALUES (?, 'm2-app', 'saved', ?, ?, 'user')", (self.legacy_job, STAMP, STAMP))
            conn.execute("INSERT INTO career_outreach (draft_id, application_id, job_id, short_message, created_at) "
                         "VALUES (900, 'm2-app', ?, 'Old draft', ?)", (self.legacy_job, STAMP))
        self.job_id = career_store.upsert_job(JOB)
        self.before = self.old_rows()

    def old_rows(self):
        with closing(sqlite3.connect(self.directory / 'agent.sqlite3')) as conn:
            return (conn.execute('SELECT * FROM career_applications ORDER BY id').fetchall(),
                    conn.execute('SELECT * FROM job_events ORDER BY id').fetchall())

    def test_outreach_the_chat_tools_and_the_send_flow_write_only_the_tracker(self):
        tools = CareerGraphTools()
        saved = tools.save_application({'selected_job_id': self.job_id, 'notes': 'from the chat'})
        tools.update_application({'application_id': saved['id'], 'application_status': 'APPLIED', 'notes': 'applied myself'})
        draft = tools.prepare_outreach({'selected_job_id': self.job_id, 'recipient': 'recruiter@example.org'})
        self.assertEqual(draft['application_id'], saved['id'])
        self.assertEqual(self.client.post(f"/email/drafts/{draft['id']}/approve", headers=LOCAL).status_code, 200)
        gmail = Mock(return_value={'id': 'fictional-gmail-id'})
        with patch.object(main, 'send_approved_email', gmail):
            self.assertEqual(self.client.post(f"/email/drafts/{draft['id']}/send", headers=LOCAL).status_code, 200)
        gmail.assert_called_once()

        self.assertEqual(self.old_rows(), self.before, 'career_applications and job_events are unchanged')
        current = career_store.get_application(saved['id'])
        self.assertEqual((current['status'], current['notes'], current['outreach_state']), ('APPLIED', 'applied myself', 'SENT'))
        self.assertEqual([event['event_type'] for event in current['timeline']],
                         ['saved', 'applied', 'outreach_prepared', 'outreach_sent'])
        listed = self.client.get('/api/v1/applications', headers=LOCAL).json()['applications']
        self.assertEqual([row['id'] for row in listed], [saved['id']], 'the dashboard (/api/v1) sees the chat\'s application')

    def test_a_link_from_before_the_tracker_import_still_completes_a_send(self):
        career_store.mark_outreach_sent(900)
        self.assertIsNotNone(career_store.get_outreach_for_draft(900)['sent_at'])
        self.assertEqual(self.old_rows(), self.before)
        self.assertEqual(service.list_applications(store.LOCAL_USER_ID), [])

    def test_only_thumbs_and_removal_notes_are_added_to_job_events(self):
        career_store.save_application(self.job_id)
        career_store.record_thumb(self.legacy_job, 'up', request_id='thumb-1')
        alert_store.upsert([JobPosting.model_validate(JOB)], kind='capture')
        self.assertEqual(self.client.delete(f'/capture/jobs/{self.job_id}', headers=LOCAL).status_code, 200)
        applications, events = self.old_rows()
        self.assertEqual(applications, self.before[0])
        self.assertEqual(events[:len(self.before[1])], self.before[1])
        added = [(row[1], row[3]) for row in events[len(self.before[1]):]]
        self.assertEqual(added, [(self.legacy_job, 'thumbs_up'), (self.job_id, 'removed_from_results')])

    def test_the_event_log_refuses_application_and_outreach_events(self):
        with career_store._connection() as conn:
            for event_type in ('saved', 'applied', 'interview', 'recruiter_reply', 'outreach_prepared', 'outreach_sent'):
                with self.assertRaises(career_events.ReadOnlyEventError, msg=event_type):
                    career_events.append_event(conn, self.job_id, event_type)
            [saved] = [row for row in career_events.events_for_job(conn, self.legacy_job) if row['event_type'] == 'saved']
            with self.assertRaises(career_events.ReadOnlyEventError, msg='undoing an M2 status event'):
                career_events.append_event(conn, self.legacy_job, 'undone', undoes_event_id=saved['id'])
        self.assertEqual(self.old_rows(), self.before)

    def test_no_app_code_writes_career_applications_outside_its_migrations(self):
        writes = re.compile(r'(INSERT\s+(OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)\s+career_applications\b', re.IGNORECASE)
        found = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / 'app').rglob('*.py') if writes.search(path.read_text(encoding='utf-8')))
        self.assertEqual(found, ['app/storage/career_events.py'], 'only the v3 backfill migration (career_events.migrate_v3)')


class OutreachAfterSendTests(_IsolatedApp):
    """Once Gmail has confirmed a send, the tracker cannot turn it into an uncertain one."""

    def setUp(self):
        super().setUp()
        for module in (email_store, career_store):
            patcher = patch.object(module, 'DB_PATH', self.directory / 'agent.sqlite3')
            patcher.start()
            self.addCleanup(patcher.stop)
        self.job_id = career_store.upsert_job(JOB)
        self.application = career_store.save_application(self.job_id)

    def test_a_tracker_failure_after_a_confirmed_send_keeps_the_draft_sent(self):
        draft = email_store.create_draft('recruiter@example.org', 'Hello', 'Review me', None)
        career_store.link_outreach(self.application['id'], draft['id'], 'Hello')
        email_store.approve_draft(draft['id'])
        gmail = Mock(return_value={'id': 'fictional-gmail-id'})
        with patch.object(main, 'send_approved_email', gmail), \
                patch.object(service, 'record_outreach', side_effect=RuntimeError('tracker locked')), \
                self.assertLogs('app.storage.career_store', 'WARNING') as logs:
            response = self.client.post(f"/email/drafts/{draft['id']}/send", headers=LOCAL)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(email_store.get_draft(draft['id'])['status'], 'sent')
        self.assertIsNotNone(career_store.get_outreach_for_draft(draft['id'])['sent_at'])
        self.assertEqual(logs.output, [f"WARNING:app.storage.career_store:outreach_sent for draft {draft['id']} "
                                       'not recorded in the tracker (RuntimeError)'])

    def test_a_draft_id_reused_after_restoring_agent_sqlite3_is_a_new_outreach(self):
        career_store.link_outreach(self.application['id'], 5, 'First')
        career_store.mark_outreach_sent(5)
        with career_store._connection() as conn:        # agent.sqlite3 restored from a backup taken before draft 5
            conn.execute('DELETE FROM career_outreach WHERE draft_id=5')
        other = career_store.save_application(career_store.upsert_job({**JOB, 'title': 'Other Analyst',
                                                                        'application_url': 'https://careers.example.org/jobs/8'}))
        career_store.link_outreach(other['id'], 5, 'Second')
        career_store.mark_outreach_sent(5)
        self.assertEqual([event['event_type'] for event in career_store.get_application(other['id'])['timeline']],
                         ['saved', 'outreach_prepared', 'outreach_sent'])
