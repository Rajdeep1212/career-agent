"""Offline outreach drafting and atomic send claims; no mail is sent."""
import importlib
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

from fastapi.testclient import TestClient

from app.models.schemas import CandidateProfile
from app.models.career import ContactCandidate


class OutreachTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        old_config = sys.modules.get('app.core.config')
        sys.modules['app.core.config'] = SimpleNamespace(settings=SimpleNamespace(data_dir=self.temp.name))
        try:
            self.store = importlib.import_module('app.storage.email_store')
            self.career = importlib.import_module('app.storage.career_store')
            self.profiles = importlib.import_module('app.storage.profile_store')
            self.composer = importlib.import_module('app.services.email_composer')
        finally:
            if old_config is None:
                sys.modules.pop('app.core.config', None)
            else:
                sys.modules['app.core.config'] = old_config
        for target, name, path in [(self.store, 'DB_PATH', 'agent.sqlite3'), (self.career, 'DB_PATH', 'agent.sqlite3'), (self.profiles, 'PROFILE_PATH', 'profile.json')]:
            patcher = patch.object(target, name, Path(self.temp.name) / path)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.profile = CandidateProfile(name='Alex Example', graduation_year=2024, degree='BA Economics', skills=['Excel', 'SQL', 'Bookkeeping'], projects=['Created a budget dashboard in Excel'], research=['Surveyed local transport access'])
        self.job = {'company': 'ExampleCo', 'title': 'Finance Analyst', 'location': 'Oslo', 'skills': ['Excel', 'Bookkeeping'], 'application_url': 'https://example.org/jobs/1'}

    def service(self):
        self.assertIsNotNone(importlib.util.find_spec('app.services.outreach_service'), 'outreach service must exist')
        return importlib.import_module('app.services.outreach_service')

    def test_legacy_composer_uses_actual_profile_facts(self):
        self.profiles.save_profile(self.profile)
        subject, body = self.composer.compose_job_application_email('hr@example.org', 'ExampleCo', 'Finance Analyst')
        self.assertIn('Alex Example', subject)
        self.assertIn('2024', body)
        self.assertIn('BA Economics', body)
        self.assertIn('Bookkeeping', body)
        self.assertNotIn('Example Research Institute', body)
        self.assertNotIn('2025', body)
        self.assertNotIn('production-oriented AI', body)
        self.assertNotIn('attached', body.lower())

    def test_draft_grounded_in_non_ai_skill_overlap(self):
        draft = self.service().draft_outreach(self.profile, self.job, 'hr@example.org')
        self.assertEqual(set(draft), {'subject', 'body', 'short_message'})
        self.assertIn('Bookkeeping', draft['body'])
        self.assertIn(self.profile.projects[0], draft['body'])
        self.assertIn(self.profile.research[0], draft['body'])
        for invented in ['Python', 'B.Tech', 'IIT', 'already applied', 'attached']:
            self.assertNotIn(invented.lower(), draft['body'].lower())

    def test_empty_profile_does_not_invent_name_or_qualifications(self):
        draft = self.service().draft_outreach(CandidateProfile(), self.job, 'hr@example.org')
        for invented in ['Jordan Example', 'graduate', 'expertise', 'experience in', 'attached']:
            self.assertNotIn(invented.lower(), draft['body'].lower())

    def test_attachment_and_custom_note_are_truthful(self):
        draft = self.service().draft_outreach(self.profile, self.job, 'hr@example.org', custom_note='Available from October.', has_attachment=True)
        self.assertIn('attached', draft['body'].lower())
        self.assertIn('Available from October.', draft['body'])
        self.assertNotIn('\\n', draft['body'])

    def test_rejects_recipient_and_subject_header_injection(self):
        service = self.service()
        for recipient in ['hr@example.org\r\nBcc: victim@example.org', 'not-an-email', 'a@example.org,b@example.org']:
            with self.assertRaises(ValueError):
                service.draft_outreach(self.profile, self.job, recipient)
        with self.assertRaises(ValueError):
            service.draft_outreach(self.profile, dict(self.job, title='Analyst\r\nBcc: x@example.org'), 'hr@example.org')
        with self.assertRaises(ValueError):
            self.store.create_draft('hr@example.org', 'Subject\nBcc: x@example.org', 'Body', None)

    def test_options_no_contact_does_not_guess(self):
        options = self.service().find_outreach_options(self.job)
        self.assertEqual(options['contacts'], [])
        self.assertTrue(options['message'])
        self.assertNotIn('@', options['message'])

    def test_options_only_return_contacts_for_job_company(self):
        self.career.save_contact(ContactCandidate(company='ExampleCo', contact_method='hiring@example.org', public_source='Provided by user'))
        self.career.save_contact(ContactCandidate(company='OtherCo', contact_method='other@example.org', public_source='Provided by user'))
        options = self.service().find_outreach_options(self.job)
        self.assertEqual(len(options['contacts']), 1)
        self.assertEqual(options['contacts'][0]['contact_method'], 'hiring@example.org')
        self.assertEqual(options['contacts'][0]['confidence'], 'user_provided')

    def draft(self):
        return self.store.create_draft('hr@example.org', 'Hello', 'Review me', None)

    def test_claim_requires_explicit_approval(self):
        draft = self.draft()
        self.assertTrue(hasattr(self.store, 'claim_draft_for_send'), 'atomic claim required')
        self.assertIsNone(self.store.claim_draft_for_send(draft['id']))
        self.assertEqual(self.store.approve_draft(draft['id'])['status'], 'approved')
        self.assertEqual(self.store.claim_draft_for_send(draft['id'])['status'], 'sending')
        self.assertIsNone(self.store.claim_draft_for_send(draft['id']))

    def test_concurrent_claim_has_single_winner(self):
        draft = self.draft()
        self.assertTrue(hasattr(self.store, 'claim_draft_for_send'), 'atomic claim required')
        self.store.approve_draft(draft['id'])
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.claim_draft_for_send(draft['id']), range(8)))
        self.assertEqual(sum(value is not None for value in results), 1)

    def test_uncertain_send_failure_cannot_be_automatically_retried(self):
        draft = self.draft()
        self.assertTrue(hasattr(self.store, 'mark_send_failed'), 'uncertain send failure state required')
        self.store.approve_draft(draft['id'])
        self.store.claim_draft_for_send(draft['id'])
        self.assertEqual(self.store.mark_send_failed(draft['id'])['status'], 'send_failed')
        self.assertIsNone(self.store.claim_draft_for_send(draft['id']))
        self.assertIsNone(self.store.approve_draft(draft['id']))

    def test_cancel_and_success_preserve_existing_workflow(self):
        draft = self.draft()
        self.assertIsNone(self.store.mark_sent(draft['id'], 'must-not-send'))
        self.assertEqual(self.store.get_draft(draft['id'])['status'], 'draft')
        self.assertEqual(self.store.cancel_draft(draft['id'])['status'], 'cancelled')
        self.assertIsNone(self.store.approve_draft(draft['id']))
        self.assertTrue(hasattr(self.store, 'claim_draft_for_send'), 'atomic claim required')
        draft = self.draft()
        self.store.approve_draft(draft['id'])
        self.store.claim_draft_for_send(draft['id'])
        sent = self.store.mark_sent(draft['id'], 'fictional-gmail-id')
        self.assertEqual(sent['status'], 'sent')
        self.assertEqual(sent['gmail_message_id'], 'fictional-gmail-id')
        self.assertIsNotNone(sent['sent_at'])
        self.assertIsNone(self.store.cancel_draft(draft['id']))


class EmailSendAPITests(unittest.TestCase):
    origin = {'Origin': 'http://localhost:8010'}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        from app import main
        from app.storage import career_store, email_store, profile_store
        self.main = main
        self.career = career_store
        self.store = email_store
        for module, name, value in (
            (email_store, 'DB_PATH', directory / 'agent.sqlite3'),
            (career_store, 'DB_PATH', directory / 'agent.sqlite3'),
            (profile_store, 'PROFILE_PATH', directory / 'profile.json'),
        ):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(main.app, base_url='http://localhost:8010', raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def stored_job(self, *, company='StoredCo', title='Stored Analyst', suffix='1'):
        return self.career.upsert_job({
            'company': company,
            'title': title,
            'location': 'Remote',
            'skills': ['SQL', 'Excel'],
            'description': 'Use SQL and Excel for reporting.',
            'application_url': f'https://jobs.example.org/{suffix}',
        })

    def prepare(self, **overrides):
        payload = {
            'recipient': 'recruiter@stored.example',
            'company': 'Frontend Substitute Co',
            'title': 'Frontend Substitute Role',
            'application_url': 'https://attacker.example/substitute',
            **overrides,
        }
        return self.client.post('/agent/prepare-email', json=payload, headers=self.origin)

    def draft(self):
        return self.store.create_draft('hr@example.org', 'Application', 'Review me', None)

    def post_send(self, draft_id, client=None):
        return (client or self.client).post(f'/email/drafts/{draft_id}/send', headers=self.origin)

    def test_creation_approval_and_cancel_require_exact_local_origin(self):
        payload = {'recipient': 'hr@example.org', 'subject': 'Hello', 'body': 'Review me'}
        self.assertEqual(self.client.post('/email/drafts', json=payload).status_code, 403)
        self.assertEqual(self.client.post('/email/drafts', json=payload, headers={'Origin': 'https://attacker.example'}).status_code, 403)
        self.assertEqual(self.client.post('/email/drafts', json=payload, headers=self.origin).status_code, 200)

        prepare = {'recipient': 'hr@example.org', 'company': 'ExampleCo', 'title': 'Analyst'}
        self.assertEqual(self.client.post('/agent/prepare-email', json=prepare).status_code, 403)
        self.assertEqual(self.client.post('/agent/prepare-email', json=prepare, headers=self.origin).status_code, 200)

        draft = self.draft()
        self.assertEqual(self.client.post(f"/email/drafts/{draft['id']}/approve").status_code, 403)
        self.assertEqual(self.store.get_draft(draft['id'])['status'], 'draft')
        self.assertEqual(self.client.post(f"/email/drafts/{draft['id']}/cancel", headers={'Origin': 'https://attacker.example'}).status_code, 403)
        self.assertEqual(self.client.post(f"/email/drafts/{draft['id']}/approve", headers=self.origin).status_code, 200)
        gmail = Mock(return_value={'id': 'must-not-send'})
        with patch.object(self.main, 'send_approved_email', gmail):
            self.assertEqual(self.client.post(f"/email/drafts/{draft['id']}/send").status_code, 403)
            self.assertEqual(self.client.post(
                f"/email/drafts/{draft['id']}/send",
                headers={'Origin': 'https://attacker.example'},
            ).status_code, 403)
        gmail.assert_not_called()
        self.assertEqual(self.store.get_draft(draft['id'])['status'], 'approved')
        self.assertEqual(self.client.post(f"/email/drafts/{draft['id']}/cancel", headers=self.origin).status_code, 200)

    def test_prepare_uses_authoritative_job_and_persists_application_linkage(self):
        job_id = self.stored_job()
        application = self.career.save_application(job_id, status='SAVED')

        response = self.prepare(job_id=job_id, application_id=application['id'])

        self.assertEqual(response.status_code, 200, response.text)
        draft = response.json()
        self.assertEqual(draft['job_id'], job_id)
        self.assertEqual(draft['application_id'], application['id'])
        self.assertIsNone(draft['contact_id'])
        self.assertIn('Stored Analyst', draft['subject'])
        self.assertIn('StoredCo', draft['body'])
        self.assertIn('https://jobs.example.org/1', draft['body'])
        self.assertNotIn('Frontend Substitute', draft['subject'] + draft['body'])
        self.assertNotIn('attacker.example', draft['body'])
        self.assertTrue(draft['short_message'])

        reloaded = self.client.get(f"/email/drafts/{draft['id']}")
        self.assertEqual(reloaded.status_code, 200)
        self.assertEqual(reloaded.json()['application_id'], application['id'])
        self.assertEqual(reloaded.json()['short_message'], draft['short_message'])
        tracked = self.career.get_application(application['id'])
        self.assertEqual(tracked['status'], 'SAVED')
        self.assertIsNone(tracked['applied_at'])
        self.assertEqual(tracked['outreach_state'], 'PREPARED')
        self.assertEqual(tracked['outreach'][0]['draft_id'], draft['id'])

        with closing(sqlite3.connect(self.store.DB_PATH)) as conn:
            persisted = conn.execute(
                'SELECT application_id, contact_id, short_message FROM career_outreach WHERE draft_id=?',
                (draft['id'],),
            ).fetchone()
        self.assertEqual(persisted, (application['id'], None, draft['short_message']))

    def test_prepare_known_job_idempotently_creates_one_saved_application(self):
        job_id = self.stored_job(suffix='recommendation')

        first = self.prepare(job_id=job_id)
        second = self.prepare(job_id=job_id)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()['application_id'], second.json()['application_id'])
        self.assertNotEqual(first.json()['id'], second.json()['id'])
        applications = self.career.list_applications()
        self.assertEqual(len(applications), 1)
        self.assertEqual(applications[0]['status'], 'SAVED')
        self.assertEqual(applications[0]['outreach_state'], 'PREPARED')
        self.assertEqual(len(applications[0]['outreach']), 2)

    def test_prepare_rejects_unknown_malformed_and_mismatched_references(self):
        first_job = self.stored_job(company='FirstCo', suffix='first')
        second_job = self.stored_job(company='SecondCo', suffix='second')
        second_application = self.career.save_application(second_job)
        other_contact = self.career.save_contact(ContactCandidate(
            company='OtherCo',
            contact_method='other@example.org',
            public_source='Provided by user',
        ))

        cases = [
            ({'job_id': '0' * 64}, 404),
            ({'application_id': '00000000-0000-4000-8000-000000000000'}, 404),
            ({'job_id': first_job, 'contact_id': '00000000-0000-4000-8000-000000000000'}, 404),
            ({'job_id': first_job, 'application_id': second_application['id']}, 409),
            ({'job_id': first_job, 'contact_id': other_contact['id']}, 409),
            ({'job_id': 'not-a-job-id'}, 422),
            ({'application_id': 'not-an-application-id'}, 422),
            ({'job_id': first_job, 'contact_id': 'not-a-contact-id'}, 422),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                response = self.prepare(**payload)
                self.assertEqual(response.status_code, expected, response.text)
                self.assertNotIn('sqlite', response.text.lower())

        self.assertEqual(self.career.list_applications(), [second_application])
        self.assertEqual(self.store.list_drafts(), [])

    def test_prepare_contact_is_authoritative_and_linked_to_company(self):
        job_id = self.stored_job()
        contact = self.career.save_contact(ContactCandidate(
            company='StoredCo',
            name='Stored Person',
            contact_method='stored.person@example.org',
            public_source='https://stored.example/team',
        ))

        response = self.prepare(
            job_id=job_id,
            contact_id=contact['id'],
            recipient='substitute@example.org',
        )

        self.assertEqual(response.status_code, 200, response.text)
        draft = response.json()
        self.assertEqual(draft['recipient'], 'stored.person@example.org')
        self.assertEqual(draft['contact_id'], contact['id'])
        tracked = self.career.get_application(draft['application_id'])
        self.assertEqual(tracked['outreach'][0]['contact_id'], contact['id'])

    def test_prepared_linkage_is_resolved_by_existing_send_flow(self):
        job_id = self.stored_job()
        prepared = self.prepare(job_id=job_id).json()
        self.assertEqual(
            self.client.post(f"/email/drafts/{prepared['id']}/approve", headers=self.origin).status_code,
            200,
        )

        gmail = Mock(return_value={'id': 'gmail-linked-message'})
        with patch.object(self.main, 'send_approved_email', gmail):
            sent = self.post_send(prepared['id'])

        self.assertEqual(sent.status_code, 200, sent.text)
        application = self.career.get_application(prepared['application_id'])
        self.assertEqual(application['status'], 'SAVED')
        self.assertIsNone(application['applied_at'])
        self.assertEqual(application['outreach_state'], 'SENT')
        self.assertIsNotNone(application['outreach'][0]['sent_at'])

    def test_non_claimable_states_never_call_gmail(self):
        drafts = {'draft': self.draft()}
        drafts['cancelled'] = self.draft()
        self.store.cancel_draft(drafts['cancelled']['id'])
        drafts['sending'] = self.draft()
        self.store.approve_draft(drafts['sending']['id'])
        self.store.claim_draft_for_send(drafts['sending']['id'])
        drafts['send_failed'] = self.draft()
        self.store.approve_draft(drafts['send_failed']['id'])
        self.store.claim_draft_for_send(drafts['send_failed']['id'])
        self.store.mark_send_failed(drafts['send_failed']['id'])
        drafts['sent'] = self.draft()
        self.store.approve_draft(drafts['sent']['id'])
        self.store.claim_draft_for_send(drafts['sent']['id'])
        self.store.mark_sent(drafts['sent']['id'], 'already-sent')

        gmail = Mock(return_value={'id': 'must-not-send'})
        with patch.object(self.main, 'send_approved_email', gmail):
            self.assertEqual(self.post_send(999999).status_code, 404)
            for state, draft in drafts.items():
                with self.subTest(state=state):
                    self.assertEqual(self.post_send(draft['id']).status_code, 409)
        gmail.assert_not_called()

    def test_successful_send_marks_draft_and_tracker_without_marking_applied(self):
        job_id = self.career.upsert_job({
            'company': 'ExampleCo', 'title': 'Analyst', 'location': 'Remote',
            'application_url': 'https://example.org/jobs/1',
        })
        application = self.career.save_application(job_id, status='SAVED')
        draft = self.draft()
        self.career.link_outreach(application['id'], draft['id'], 'Hello from candidate')
        self.store.approve_draft(draft['id'])

        gmail = Mock(return_value={'id': 'gmail-message-123'})
        with patch.object(self.main, 'send_approved_email', gmail):
            response = self.post_send(draft['id'])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'sent')
        self.assertEqual(response.json()['gmail_message_id'], 'gmail-message-123')
        gmail.assert_called_once()
        tracked = self.career.get_application(application['id'])
        self.assertEqual(tracked['status'], 'SAVED')
        self.assertIsNone(tracked['applied_at'])
        self.assertEqual(tracked['outreach_state'], 'SENT')
        self.assertIsNotNone(tracked['outreach'][0]['sent_at'])

    def test_uncertain_failure_is_terminal_and_error_is_safe(self):
        draft = self.draft()
        self.store.approve_draft(draft['id'])
        gmail = Mock(side_effect=RuntimeError('secret-provider-token-and-body'))
        with patch.object(self.main, 'send_approved_email', gmail):
            first = self.post_send(draft['id'])
            second = self.post_send(draft['id'])

        self.assertEqual(first.status_code, 503)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(self.store.get_draft(draft['id'])['status'], 'send_failed')
        gmail.assert_called_once()
        self.assertNotIn('secret-provider-token-and-body', first.text)
        self.assertNotIn('secret-provider-token-and-body', second.text)

    def test_concurrent_send_requests_call_gmail_once(self):
        draft = self.draft()
        self.store.approve_draft(draft['id'])
        entered = threading.Event()
        release = threading.Event()

        def send_once(**_kwargs):
            entered.set()
            release.wait(timeout=2)
            return {'id': 'gmail-concurrent'}

        gmail = Mock(side_effect=send_once)

        def request():
            with TestClient(self.main.app, base_url='http://localhost:8010', raise_server_exceptions=False) as client:
                return self.post_send(draft['id'], client)

        with patch.object(self.main, 'send_approved_email', gmail), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(request)
            self.assertTrue(entered.wait(timeout=2))
            second = pool.submit(request)
            time.sleep(0.1)
            release.set()
            responses = [first.result(timeout=2), second.result(timeout=2)]

        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        gmail.assert_called_once()
        self.assertEqual(self.store.get_draft(draft['id'])['status'], 'sent')


if __name__ == '__main__':
    unittest.main()
