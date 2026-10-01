"""Q2b2: freshness follow-up (docs/ROADMAP_QUEUE.md): the alert email's arrival date as a "seen on"
date, relative "posted N days ago" text, a notice when the index sync is stale. Fixed dates,
fictional data, temporary databases; no network."""
import unittest
from datetime import date
from unittest.mock import patch

from app.providers import jsearch_provider
from app.services.freshness import freshness, resolve_relative
from app.sources.alerts.parser import AlertJob, ParsedAlert
from app.storage import alert_store, radar_store
from test_freshness import TODAY, SearchFreshnessTests, _Stores, job


def verdict(**fields):
    result = freshness(job(**fields), TODAY)
    return result.state, result.decision, result.age_days, result.dated_by


class RelativeDateTests(unittest.TestCase):
    def test_relative_text_becomes_a_day_counted_from_the_reference(self):
        for text, expected in (('3 days ago', '2026-09-28'), ('Posted 3 days ago', '2026-09-28'), ('1 day ago', '2026-09-30'),
                               ('yesterday', '2026-09-30'), ('today', '2026-10-01'), ('Just posted', '2026-10-01'),
                               ('just now', '2026-10-01'), ('5 hours ago', '2026-10-01'), ('20 minutes ago', '2026-10-01'),
                               ('2 weeks ago', '2026-09-17'), ('1 month ago', '2026-09-01'), ('Posted 2 months ago', '2026-08-02')):
            self.assertEqual(resolve_relative(text, TODAY), date.fromisoformat(expected), text)

    def test_n_plus_days_means_more_than_n(self):
        self.assertEqual(resolve_relative('30+ days ago', TODAY), date(2026, 8, 31), '31 days: past the 30-day limit')

    def test_text_that_is_not_a_posting_age_gives_nothing(self):
        for text in (None, '', '2026-09-20', 'Active 3 days ago', 'Employer active 2 days ago', 'Apply within 3 days', 'soon'):
            self.assertIsNone(resolve_relative(text, TODAY), text)


class SeenOnTests(unittest.TestCase):
    def test_an_undated_job_is_judged_by_the_day_its_alert_email_arrived(self):
        self.assertEqual(verdict(seen_on='2026-09-27T09:00:00+05:30'), ('unknown', 'show', 4, 'seen_on'))
        self.assertEqual(verdict(seen_on='2026-08-17T09:00:00+05:30'), ('unknown', 'check', 45, 'seen_on'))
        self.assertEqual(verdict(seen_on='2026-07-01T09:00:00+05:30'), ('unknown', 'hide', 92, 'seen_on'))
        self.assertIn('Seen on 2026-09-27', freshness(job(seen_on='2026-09-27'), TODAY).summary)

    def test_a_posted_date_wins_over_the_arrival_date(self):
        self.assertEqual(verdict(posted_date='2026-08-17', seen_on='2026-09-27'), ('unknown', 'check', 45, 'posted'))

    def test_relative_text_is_counted_from_the_day_it_was_read(self):
        self.assertEqual(verdict(posted_date='3 days ago', seen_on='2026-09-27'), ('unknown', 'show', 7, 'posted'))
        self.assertEqual(verdict(posted_date='3 days ago', captured_at='2026-08-01T10:00:00+00:00'), ('unknown', 'hide', 64, 'posted'))

    def test_relative_text_with_no_day_to_count_from_is_undated(self):
        self.assertEqual(verdict(posted_date='3 days ago'), ('unknown', 'hide', None, None))

    def test_a_job_with_no_date_at_all_names_no_basis(self):
        self.assertEqual(verdict(), ('unknown', 'hide', None, None))
        self.assertEqual(verdict(posted_date='2026-09-30')[3], 'posted')


class AlertDatesTests(_Stores):
    def alert(self, received, extra=(), job_id='indeed:1'):
        return ParsedAlert(platform='indeed', message_id=f'<{received}>', subject='Jobs', received_at=received,
                           jobs=[AlertJob('indeed', job_id, 'https://in.indeed.com/viewjob?jk=0123456789abcdef', 'ML Engineer',
                                          'ExampleCo', 'Pune', list(extra))]).postings()

    def test_an_alert_job_carries_the_arrival_date_of_its_email(self):
        [posting] = self.alert('2026-09-27T09:00:00+05:30')
        self.assertEqual((posting.seen_on, posting.posted_date), ('2026-09-27T09:00:00+05:30', None))

    def test_relative_text_in_the_email_becomes_a_posted_date(self):
        [posting] = self.alert('2026-09-27T09:00:00+05:30', extra=['0-2 Yrs', 'Posted 3 days ago'])
        self.assertEqual(posting.posted_date, '2026-09-24')
        [active] = self.alert('2026-09-27T09:00:00+05:30', extra=['Active 3 days ago'])
        self.assertIsNone(active.posted_date)

    def test_a_job_stored_before_this_change_reads_the_date_from_its_note(self):
        legacy = job(source='Indeed alert', source_job_id='indeed:9', title='ML Engineer',
                     description='From a Indeed job alert received 27 Sep 2026. Experience: 0-2 years.')
        alert_store.upsert([legacy], kind='alert')
        [stored] = alert_store.list_jobs()
        self.assertEqual(stored.seen_on, '2026-09-27')
        self.assertEqual(freshness(stored, TODAY).decision, 'show')

    def test_a_later_email_for_the_same_job_moves_the_date_forward_only(self):
        alert_store.upsert(self.alert('2026-09-20T09:00:00+05:30'), kind='alert')
        alert_store.upsert(self.alert('2026-09-27T09:00:00+05:30'), kind='alert')
        alert_store.upsert(self.alert('2026-09-22T09:00:00+05:30'), kind='alert')
        [stored] = alert_store.list_jobs()
        self.assertEqual(stored.seen_on, '2026-09-27T09:00:00+05:30')

    def test_a_saved_page_has_no_seen_on_date(self):
        alert_store.upsert([job(application_url='https://example.com/jobs/1', source='Saved by you')], kind='capture')
        self.assertIsNone(alert_store.list_jobs()[0].seen_on)


class JSearchRelativeDateTests(unittest.TestCase):
    ITEM = {'job_id': 'j1', 'job_title': 'ML Engineer', 'employer_name': 'ExampleCo', 'job_city': 'Pune', 'job_country': 'IN',
            'job_description': 'Python', 'job_apply_link': 'https://example.com/jobs/1'}

    def test_relative_text_is_resolved_on_the_day_it_is_fetched(self):
        with patch.object(jsearch_provider, 'today_ist', return_value=TODAY):
            relative = jsearch_provider.normalize_item({**self.ITEM, 'job_posted_at': '3 days ago'})
            exact = jsearch_provider.normalize_item({**self.ITEM, 'job_posted_at': '3 days ago',
                                                     'job_posted_at_datetime_utc': '2026-09-20T00:00:00.000Z'})
            unknown = jsearch_provider.normalize_item({**self.ITEM, 'job_posted_at': 'Recently'})
        self.assertEqual(relative.posted_date, '2026-09-28')
        self.assertEqual(exact.posted_date, '2026-09-20T00:00:00.000Z')
        self.assertIsNone(unknown.posted_date, 'text that is not a date is not kept as one')


class StaleSyncNoticeTests(SearchFreshnessTests):
    def setUp(self):
        super().setUp()
        radar_store.finish_run(radar_store.start_run('b', today=date(2026, 9, 28)), status='ok')

    async def test_a_sync_older_than_48_hours_is_announced_with_what_it_hid(self):
        response = await self.search(today=TODAY)
        self.assertEqual(response['diagnostics']['stale_sync'], {'last_sync_day': '2026-09-28', 'days_ago': 3})
        notice = response['sync_notice']
        for part in ('2026-09-28', '3 days ago', '2 hidden', '1 to check before applying', 'Sync now'):
            self.assertIn(part, notice)
        self.assertIn(notice, response['summary'])

    async def test_a_recent_sync_brings_no_notice(self):
        response = await self.search(today=date(2026, 9, 30))
        self.assertIsNone(response['diagnostics']['stale_sync'])
        self.assertIsNone(response['sync_notice'])

    async def test_a_failed_run_does_not_count_as_a_sync(self):
        radar_store.finish_run(radar_store.start_run('b', today=date(2026, 9, 30)), status='error')
        response = await self.search(today=TODAY)
        self.assertEqual(response['diagnostics']['stale_sync']['last_sync_day'], '2026-09-28')

    async def test_no_recorded_sync_brings_no_notice(self):
        with patch.object(radar_store, 'last_sync_day', return_value=None):
            response = await self.search(today=TODAY)
        self.assertIsNone(response['sync_notice'])


del SearchFreshnessTests   # imported as a base class only; do not run its tests twice


if __name__ == '__main__':
    unittest.main()
