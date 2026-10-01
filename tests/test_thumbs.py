"""M2 commit 6: live thumbs up/down labels in the job event log, and their export for the evaluation
harness (docs/M2_PLAN.md §4, decision Q6). Fictional data and temporary databases only."""
import importlib.util
import json
from pathlib import Path

from app.models.schemas import CandidateProfile
from app.storage import career_events, career_store, profile_store
from test_security_regressions import LOCAL, _IsolatedApp

ROOT = Path(__file__).resolve().parents[1]
JOB = {'company': 'ExampleCo', 'title': 'NLP Engineer', 'location': 'Pune', 'description': 'Python and NLP'}
SECRET = 'SENTINEL-PRIVATE-CV-LINE'


def _export_module():
    spec = importlib.util.spec_from_file_location('export_labels', ROOT / 'scripts' / 'export_labels.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Api(_IsolatedApp):
    def setUp(self):
        super().setUp()
        profile_store.save_profile(CandidateProfile(name='Test Student', skills=['Python'], summary=SECRET))
        self.job_id = career_store.upsert_job(JOB)
        self.requests = 0

    def rate(self, label, job_id=None, request_id=None, headers=LOCAL, **context):
        self.requests += 1
        body = {'label': label, 'request_id': request_id or f'thumb-{self.requests}'}
        if context:
            body['context'] = context
        return self.client.post(f'/jobs/{job_id or self.job_id}/relevance', json=body, headers=headers)

    def thumbs(self):
        return [event for event in career_store.job_events(self.job_id)
                if event['event_type'] in career_events.THUMBS_EVENTS]


class ThumbsApiTests(_Api):
    def test_the_exact_local_origin_is_required(self):
        for headers in ({}, {'Origin': 'https://attacker.example'}):
            self.assertEqual(self.rate('up', headers=headers).status_code, 403)
        self.assertEqual(self.thumbs(), [])

    def test_an_unknown_job_or_label_is_refused(self):
        self.assertEqual(self.rate('up', job_id='missing').status_code, 404)
        self.assertEqual(self.rate('maybe').status_code, 422)
        self.assertEqual(self.rate('up', position=0).status_code, 422)

    def test_a_label_on_an_unsaved_job_is_an_event_with_its_context_and_a_snapshot(self):
        response = self.rate('up', search_session_id='session-1', position=3)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['label'], 'up')
        [event] = self.thumbs()
        self.assertEqual(event['event_type'], 'thumbs_up')
        self.assertIsNone(event['application_id'], 'no application is created by a label')
        self.assertEqual(career_store.list_applications(), [])
        self.assertIsNotNone(event['snapshot_id'])
        context = json.loads(event['context_json'])
        self.assertEqual(context, {'scale': 'thumbs', 'rubric_version': 'thumbs-v1', 'search_session_id': 'session-1',
                                   'position': 3, 'ranker_version': career_events.ranker_version()})

    def test_the_latest_label_wins_and_clear_removes_it(self):
        self.rate('up')
        self.assertEqual(self.rate('down').json()['label'], 'down')
        self.assertEqual(self.client.get('/jobs/relevance').json()['labels'], {self.job_id: 'down'})
        self.assertIsNone(self.rate('clear').json()['label'])
        self.assertEqual(self.client.get('/jobs/relevance').json()['labels'], {})
        self.assertEqual([event['event_type'] for event in self.thumbs()], ['thumbs_up', 'thumbs_down', 'thumbs_cleared'])

    def test_repeats_record_nothing(self):
        first = self.rate('up', request_id='same').json()
        self.assertTrue(self.rate('up', request_id='same').json()['replayed'])
        self.assertTrue(self.rate('up').json()['already_recorded'])
        self.rate('clear')
        self.assertTrue(self.rate('clear').json()['already_recorded'], 'nothing to clear')
        self.assertFalse(first['already_recorded'])
        self.assertEqual(len(self.thumbs()), 2)
        self.assertEqual(self.rate('down', request_id='same').status_code, 409)

    def test_an_unchanged_job_is_snapshotted_once(self):
        for label in ('up', 'down', 'up', 'clear', 'down'):
            self.rate(label)
        labelled = [event for event in self.thumbs() if event['event_type'] != 'thumbs_cleared']
        self.assertEqual(len(labelled), 4)
        self.assertEqual(len({event['snapshot_id'] for event in labelled}), 1)
        self.assertIsNone(self.thumbs()[3]['snapshot_id'], 'clearing a label needs no snapshot')

    def test_a_label_changes_neither_the_tracker_nor_the_funnel(self):
        application = career_store.save_application(self.job_id, status='APPLIED')
        self.rate('down')
        current = career_store.get_application(application['id'])
        self.assertEqual((current['status'], current['response_state'], current['shortlisted']),
                         ('APPLIED', 'PENDING_CENSORED', False))
        self.assertEqual(current['last_event']['event_type'], 'applied')
        self.assertEqual(self.thumbs()[0]['application_id'], application['id'])
        self.assertEqual(career_store.funnel()['counts']['applied'], 1)

    def test_the_scale_is_named_and_never_graded(self):
        body = self.client.get('/jobs/relevance').json()
        self.assertEqual((body['scale'], body['rubric_version']), ('thumbs', 'thumbs-v1'))


class ExportTests(_Api):
    def test_the_export_is_one_json_line_per_current_label_without_cv_text(self):
        other = career_store.upsert_job({**JOB, 'company': 'OtherCo'})
        cleared = career_store.upsert_job({**JOB, 'company': 'ClearedCo'})
        self.rate('up', search_session_id='session-1', position=2)
        self.rate('down', job_id=other)
        self.rate('up', job_id=cleared)
        self.rate('clear', job_id=cleared)
        target = _export_module().export(self.directory / 'eval' / 'labels')
        self.assertEqual(target.parent, self.directory / 'eval' / 'labels')
        self.assertRegex(target.name, r'^inapp_\d{8}T\d{6}Z\.jsonl$')
        text = target.read_text(encoding='utf-8')
        rows = [json.loads(line) for line in text.splitlines()]
        self.assertEqual({row['job_id']: row['label'] for row in rows}, {self.job_id: 'up', other: 'down'})
        first = next(row for row in rows if row['job_id'] == self.job_id)
        self.assertEqual((first['scale'], first['rubric_version'], first['position'], first['search_session_id']),
                         ('thumbs', 'thumbs-v1', 2, 'session-1'))
        for key in ('snapshot_id', 'job_sha256', 'cv_version', 'ranker_version', 'vocabulary_version', 'labelled_at'):
            self.assertTrue(first[key], key)
        self.assertEqual((first['title'], first['company']), ('NLP Engineer', 'ExampleCo'))
        self.assertNotIn(SECRET, text, 'the CV is referenced by its version hash only')
        self.assertNotIn('Test Student', text)

    def test_an_export_with_no_labels_is_an_empty_file(self):
        target = _export_module().export(self.directory / 'eval' / 'labels')
        self.assertEqual(target.read_text(encoding='utf-8'), '')
