"""M2 commit 3: one-click Applied and outcome events with idempotency and undo (docs/M2_PLAN.md §3).

The M2 tracker routes were retired at the TRK3b cutover (the tracker is /api/v1, tested in test_tracker_api.py). The
store functions they wrapped are still in app/storage/career_store.py, because outreach and the chat use that store,
so these tests now drive the store directly, with the error mapping the routes had. Route-only checks (origin,
request validation) went with the routes. Fictional data and temporary databases only."""
import random
from unittest.mock import patch

from app.api.career import _snapshot, public_career_data
from app.storage import career_events, career_store
from test_security_regressions import _IsolatedApp

JOB = {'company': 'ExampleCo', 'title': 'NLP Engineer', 'location': 'Pune', 'description': 'Python and NLP'}
OUTCOMES = ('recruiter_reply', 'online_test', 'interview', 'offer', 'rejected', 'withdrawn')


class _Reply:
    """What a retired route would have answered: a status code and a JSON body."""
    def __init__(self, status_code, body):
        self.status_code, self._body, self.text = status_code, body, str(body)

    def json(self):
        return self._body


class _Api(_IsolatedApp):
    def setUp(self):
        super().setUp()
        self.job_id = career_store.upsert_job(JOB)
        self.requests = 0

    def request_id(self):
        self.requests += 1
        return f'req-{self.requests:04d}'

    @staticmethod
    def _call(action):
        try:
            return _Reply(200, public_career_data(action()))
        except LookupError as exc:
            return _Reply(404, {'detail': str(exc)})
        except career_store.RequestConflictError as exc:
            return _Reply(409, {'detail': str(exc)})
        except ValueError as exc:
            return _Reply(422, {'detail': str(exc)})
        except Exception as exc:        # what the server would have turned into a 500
            return _Reply(500, {'detail': type(exc).__name__})

    def apply(self, job_id=None, request_id=None, **fields):
        job_id = job_id or self.job_id
        if career_store.get_job(job_id) is None:
            return _Reply(404, {'detail': 'Job not found'})
        return self._call(lambda: career_store.record_applied(job_id, request_id=request_id or self.request_id(),
                                                              snapshot=_snapshot(job_id), **fields))

    def outcome(self, application_id, event_type, request_id=None, **fields):
        current = career_store.get_application(application_id)
        if current is None:
            return _Reply(404, {'detail': 'Application not found'})
        return self._call(lambda: career_store.record_outcome(application_id, event_type, request_id=request_id or self.request_id(),
                                                               snapshot=_snapshot(current['job_id']), **fields))

    def undo(self, application_id, event_id, request_id=None):
        return self._call(lambda: career_store.undo_event(application_id, event_id, request_id=request_id or self.request_id()))

    def events(self, *types):
        events = career_store.job_events(self.job_id)
        return [event for event in events if not types or event['event_type'] in types]


class AppliedTests(_Api):
    def test_an_unknown_job_or_application_is_a_404(self):
        self.assertEqual(self.apply(job_id='missing').status_code, 404)
        self.assertEqual(self.outcome('missing', 'interview').status_code, 404)
        self.assertEqual(self.undo('missing', 1).status_code, 404)

    def test_one_click_creates_the_application_with_one_applied_event_and_a_snapshot(self):
        response = self.apply(applied_via='company_site', effort_minutes=25, note='Referral form too')
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body['already_applied'], body['replayed']), (False, False))
        self.assertEqual(body['application']['status'], 'APPLIED')
        self.assertEqual(body['application']['response_state'], 'PENDING_CENSORED')
        self.assertEqual((body['application']['applied_via'], body['application']['effort_minutes']), ('company_site', 25))
        [event] = self.events()
        self.assertEqual((event['event_type'], event['source'], event['note']), ('applied', 'user', 'Referral form too'))
        self.assertEqual(body['application']['last_event']['id'], event['id'], 'the tracker offers Undo on it')
        self.assertIsNotNone(event['snapshot_id'], 'no saved event is invented; the applied event carries the snapshot')

    def test_the_same_request_twice_is_one_event(self):
        first = self.apply(request_id='same-click').json()
        again = self.apply(request_id='same-click').json()
        self.assertEqual(again['event']['id'], first['event']['id'])
        self.assertTrue(again['replayed'])
        self.assertEqual(len(self.events('applied')), 1)

    def test_a_second_click_on_an_applied_job_records_nothing(self):
        self.apply()
        again = self.apply().json()
        self.assertTrue(again['already_applied'])
        self.assertEqual(len(self.events('applied')), 1)

    def test_a_request_id_cannot_be_reused_for_something_else(self):
        other = career_store.upsert_job({**JOB, 'company': 'OtherCo'})
        self.apply(request_id='reused')
        self.assertEqual(self.apply(job_id=other, request_id='reused').status_code, 409)

    def test_the_snapshot_and_the_event_are_one_transaction(self):
        with patch.object(career_events, 'append_event', side_effect=RuntimeError('disk full')):
            self.assertEqual(self.apply().status_code, 500)
        with career_store._connection() as conn:
            counts = [conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                      for table in ('job_snapshots', 'job_events', 'career_applications')]
        self.assertEqual(counts, [0, 0, 0])


class OutcomeTests(_Api):
    def setUp(self):
        super().setUp()
        self.application_id = self.apply().json()['application']['id']

    def test_each_outcome_is_one_event(self):
        for event_type in OUTCOMES:
            response = self.outcome(self.application_id, event_type)
            self.assertEqual(response.status_code, 200, (event_type, response.text))
        self.assertEqual([event['event_type'] for event in self.events()], ['applied', *OUTCOMES])
        self.assertEqual(career_store.get_application(self.application_id)['status'], 'WITHDRAWN')

    def test_a_reply_is_a_response_that_keeps_the_status(self):
        body = self.outcome(self.application_id, 'recruiter_reply').json()
        self.assertEqual((body['application']['status'], body['application']['response_state']), ('APPLIED', 'RESPONDED'))

    def test_the_same_outcome_twice_in_a_row_records_nothing(self):
        self.outcome(self.application_id, 'interview')
        again = self.outcome(self.application_id, 'interview').json()
        self.assertTrue(again['already_recorded'])
        self.assertEqual(len(self.events('interview')), 1)

    def test_only_outcome_types_are_accepted_here(self):
        for event_type in ('applied', 'saved', 'thumbs_up', 'undone', 'outreach_sent'):
            self.assertEqual(self.outcome(self.application_id, event_type).status_code, 422, event_type)


class UndoTests(_Api):
    def setUp(self):
        super().setUp()
        applied = self.apply().json()
        self.application_id, self.applied_id = applied['application']['id'], applied['event']['id']

    def test_undoing_the_only_application_leaves_it_tracked_but_not_applied(self):
        body = self.undo(self.application_id, self.applied_id).json()
        self.assertEqual((body['application']['status'], body['application']['applied_at']), ('SAVED', None))
        self.assertIsNone(body['application']['response_state'])
        self.assertFalse(body['already_undone'])

    def test_undo_is_idempotent(self):
        first = self.undo(self.application_id, self.applied_id, request_id='undo-1').json()
        replay = self.undo(self.application_id, self.applied_id, request_id='undo-1').json()
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['event']['id'], first['event']['id'])
        again = self.undo(self.application_id, self.applied_id).json()
        self.assertTrue(again['already_undone'])
        self.assertEqual(len(self.events('undone')), 1)

    def test_applying_again_after_an_undo_works(self):
        self.undo(self.application_id, self.applied_id)
        body = self.apply().json()
        self.assertFalse(body['already_applied'])
        self.assertEqual(body['application']['status'], 'APPLIED')
        self.assertEqual(len(self.events('applied')), 2)

    def test_an_outcome_can_be_undone_and_the_status_falls_back(self):
        interview = self.outcome(self.application_id, 'interview').json()['event']['id']
        body = self.undo(self.application_id, interview).json()
        self.assertEqual(body['application']['status'], 'APPLIED')

    def test_only_this_jobs_tracker_events_can_be_undone(self):
        other_job = career_store.upsert_job({**JOB, 'company': 'OtherCo'})
        other = self.apply(job_id=other_job).json()
        self.assertEqual(self.undo(self.application_id, other['event']['id']).status_code, 404)
        self.assertEqual(self.undo(self.application_id, 999999).status_code, 404)
        undo_event = self.undo(self.application_id, self.applied_id).json()['event']['id']
        self.assertEqual(self.undo(self.application_id, undo_event).status_code, 422)

    def test_the_cache_matches_the_log_through_any_mix_of_clicks_and_undos(self):
        chooser = random.Random(930)
        for _ in range(40):
            standing = [event for event in career_events._standing(self.events())
                        if event['event_type'] in (*career_events.FUNNEL_EVENTS, *career_events.RESPONSE_EVENTS)]
            action = chooser.choice(('apply', 'outcome', 'undo'))
            if action == 'apply':
                self.apply()
            elif action == 'outcome':
                self.outcome(self.application_id, chooser.choice(OUTCOMES))
            elif standing:
                self.undo(self.application_id, chooser.choice(standing)['id'])
            events = self.events()
            current = career_store.get_application(self.application_id)
            self.assertEqual(current['status'], career_events.status_from_events(events) or 'SAVED')
            self.assertEqual(current['applied_at'], career_events.first_applied_at(events))
