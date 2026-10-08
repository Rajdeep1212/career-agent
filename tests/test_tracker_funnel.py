"""M2 commit 4: "shortlisted" is derived from the event log (docs/M2_PLAN.md §2). The M2 funnel over career_applications
was removed at TRK3c; tracker analytics are TRK8."""
import unittest
from datetime import datetime, timezone

from app.storage import career_events

NOW = datetime.now(timezone.utc)


def _event(identity, event_type, undoes=None):
    return {'id': identity, 'event_type': event_type, 'undoes_event_id': undoes, 'occurred_at': NOW.isoformat()}


class ShortlistedRuleTests(unittest.TestCase):
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
