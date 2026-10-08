"""M2 commit 4: "shortlisted" and the funnel are derived from the event log, as L0 counts
(docs/M2_PLAN.md §2). Fictional data and temporary databases only."""
from datetime import datetime, timedelta, timezone

from app.storage import career_events, career_store
from test_security_regressions import _IsolatedApp

NOW = datetime.now(timezone.utc)


def _event(identity, event_type, undoes=None):
    return {'id': identity, 'event_type': event_type, 'undoes_event_id': undoes, 'occurred_at': NOW.isoformat()}


class ShortlistedRuleTests(_IsolatedApp):
    def test_a_test_interview_or_offer_means_shortlisted(self):
        for event_type in ('online_test', 'interview', 'offer'):
            self.assertTrue(career_events.is_shortlisted([_event(1, 'applied'), _event(2, event_type)]), event_type)
        self.assertTrue(career_events.is_shortlisted([_event(1, 'offer')]), 'an offer without an interview counts')

    def test_it_stays_true_after_a_rejection_or_withdrawal(self):
        for later in ('rejected', 'withdrawn'):
            self.assertTrue(career_events.is_shortlisted(
                [_event(1, 'applied'), _event(2, 'interview'), _event(3, later)]), later)

    def test_a_reply_or_a_rejection_alone_is_not_shortlisted(self):
        for events in ([], [_event(1, 'saved')], [_event(1, 'applied')],
                       [_event(1, 'applied'), _event(2, 'recruiter_reply')],
                       [_event(1, 'applied'), _event(2, 'rejected')]):
            self.assertFalse(career_events.is_shortlisted(events))

    def test_undoing_the_only_qualifying_event_makes_it_false(self):
        self.assertFalse(career_events.is_shortlisted(
            [_event(1, 'applied'), _event(2, 'interview'), _event(3, 'undone', 2)]))
        self.assertTrue(career_events.is_shortlisted(
            [_event(1, 'applied'), _event(2, 'interview'), _event(3, 'offer'), _event(4, 'undone', 2)]))


class FunnelTests(_IsolatedApp):
    def setUp(self):
        super().setUp()
        self.requests = 0

    def rid(self):
        self.requests += 1
        return f'funnel-{self.requests}'

    def application(self, name, *, applied_days_ago=None, outcomes=(), undo_applied=False):
        job_id = career_store.upsert_job({'company': name, 'title': 'Data Analyst', 'location': 'Pune'})
        if applied_days_ago is None:
            return career_store.save_application(job_id)['id']
        applied = career_store.record_applied(
            job_id, request_id=self.rid(), occurred_at=(NOW - timedelta(days=applied_days_ago)).isoformat())
        for outcome in outcomes:
            career_store.record_outcome(applied['application']['id'], outcome, request_id=self.rid())
        if undo_applied:
            career_store.undo_event(applied['application']['id'], applied['event']['id'], request_id=self.rid())
        return applied['application']['id']

    def seed(self):
        self.shortlisted = self.application('InterviewCo', applied_days_ago=30, outcomes=('interview',))
        self.application('SilentCo', applied_days_ago=30)
        self.application('RecentCo', applied_days_ago=3)
        self.application('ReplyCo', applied_days_ago=30, outcomes=('recruiter_reply',))
        self.application('SavedCo')
        self.application('UndoneCo', applied_days_ago=30, undo_applied=True)
        self.application('RejectCo', applied_days_ago=30, outcomes=('rejected',))

    def test_counts_and_rates_come_from_the_log(self):
        self.seed()
        funnel = career_store.funnel()
        self.assertEqual(funnel['counts'], {
            'tracked': 7, 'applied': 5, 'responded': 3, 'no_response': 1, 'pending_censored': 1,
            'shortlisted': 1, 'offers': 0, 'rejected': 1, 'withdrawn': 0})
        self.assertEqual(funnel['shortlist_rate']['of_applied'], {'shortlisted': 1, 'applied': 5, 'rate': 0.2})
        self.assertEqual(funnel['shortlist_rate']['of_resolved'], {'shortlisted': 1, 'resolved': 4, 'rate': 0.25},
                         'a young application is unknown, so it is left out of the resolved rate')

    def test_the_funnel_is_labelled_as_counts_not_estimates(self):
        self.seed()
        funnel = career_store.funnel()
        self.assertEqual(funnel['claim_level'], 'L0')
        self.assertIn('not an estimate', funnel['note'])
        self.assertEqual(funnel['response_window_days'], 21)

    def test_no_applications_gives_no_rate(self):
        self.application('SavedCo')
        funnel = career_store.funnel()
        self.assertEqual(funnel['counts']['applied'], 0)
        self.assertIsNone(funnel['shortlist_rate']['of_applied']['rate'])
        self.assertIsNone(funnel['shortlist_rate']['of_resolved']['rate'])

    def test_the_funnel_route_is_retired_and_the_store_still_marks_shortlisted_applications(self):
        # TRK3b: the dashboard's funnel line is hidden until TRK8 builds analytics on the new tracker.
        self.seed()
        self.assertEqual(self.client.get('/tracker/funnel').status_code, 404)
        self.assertEqual(career_store.funnel()['counts']['shortlisted'], 1)
        flags = {entry['id']: entry['shortlisted'] for entry in career_store.list_applications()}
        self.assertEqual([identity for identity, flag in flags.items() if flag], [self.shortlisted])

