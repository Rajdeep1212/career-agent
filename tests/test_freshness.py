"""Q2b: freshness ("open today") as a pure rule on fixed dates, and its use in search
(docs/ROADMAP_QUEUE.md Q2b). Fictional data and temporary databases only; no network."""
import gc
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.models.schemas import CandidateProfile, JobPosting
from app.providers.radar_provider import RadarProvider
from app.services.career_agent import CareerAgent
from app.services.freshness import freshness, today_ist
from app.sources.adapters import posting
from app.storage import alert_store, career_store, db, history, preference_store, profile_store, radar_store, search_cache
from radar_helpers import company
from test_radar_provider import FRESHER

TODAY = date(2026, 10, 1)
SENIOR = 'Requires 10+ years of experience leading teams. Python, PyTorch and SQL.'


def job(**fields) -> JobPosting:
    return JobPosting(**{'company': 'ExampleCo', 'title': 'AI Engineer', 'location': 'Pune, India', **fields})


def verdict(**fields):
    result = freshness(job(**fields), TODAY)
    return result.state, result.decision, result.age_days


class FreshnessRuleTests(unittest.TestCase):
    def test_the_settings_are_30_and_60_days(self):
        self.assertEqual((settings.max_age_days, settings.check_age_days), (30, 60))

    def test_a_job_seen_in_its_feed_within_48_hours_is_shown_at_any_age(self):
        self.assertEqual(verdict(posted_date='2020-03-13', last_listed_on='2026-09-29'), ('open_verified', 'show', 2393))
        self.assertEqual(verdict(posted_date=None, last_listed_on='2026-10-01'), ('open_verified', 'show', None))

    def test_a_feed_sighting_older_than_48_hours_is_unknown(self):
        self.assertEqual(verdict(posted_date='2020-03-13', last_listed_on='2026-09-28', verification_state='ACTIVE_VERIFIED',
                                 verification_checked_at='2026-09-28'), ('unknown', 'hide', 2393))

    def test_a_link_check_within_48_hours_counts_only_when_it_found_the_job_open(self):
        checked = '2026-09-30T20:00:00+00:00'
        self.assertEqual(verdict(verification_state='ACTIVE_VERIFIED', verification_checked_at=checked)[:2],
                         ('open_verified', 'show'))
        for state in ('LIKELY_ACTIVE', 'UNVERIFIED'):
            self.assertEqual(verdict(verification_state=state, verification_checked_at=checked)[:2], ('unknown', 'hide'), state)

    def test_a_closed_job_is_hidden_even_when_new(self):
        self.assertEqual(verdict(posted_date='2026-10-01', verification_state='CLOSED', last_listed_on='2026-10-01')[:2],
                         ('closed', 'hide'))
        self.assertEqual(verdict(posted_date='2026-10-01', application_status='closed')[:2], ('closed', 'hide'))

    def test_an_unknown_job_is_judged_by_its_age(self):
        for posted, expected in (('2026-10-01', ('unknown', 'show', 0)), ('2026-09-01', ('unknown', 'show', 30)),
                                 ('2026-08-31', ('unknown', 'check', 31)), ('2026-08-02', ('unknown', 'check', 60)),
                                 ('2026-08-01', ('unknown', 'hide', 61)), ('2026-10-03', ('unknown', 'show', 0))):
            self.assertEqual(verdict(posted_date=posted), expected, posted)

    def test_no_posted_date_or_an_unreadable_one_is_hidden(self):
        for posted in (None, '', 'last week'):
            self.assertEqual(verdict(posted_date=posted), ('unknown', 'hide', None), posted)

    def test_dates_are_read_in_india_time(self):
        # 20:00 UTC on 31 August is already 1 September in India: 30 days old, not 31.
        self.assertEqual(verdict(posted_date='2026-08-31T20:00:00+00:00'), ('unknown', 'show', 30))
        self.assertEqual(verdict(posted_date='2026-08-31T08:00:00-04:00'), ('unknown', 'check', 31))

    def test_the_users_own_capture_is_shown_for_seven_days(self):
        self.assertEqual(verdict(captured_at='2026-09-24T10:00:00+00:00')[:2], ('unknown', 'show'))
        self.assertEqual(verdict(captured_at='2026-09-24T10:00:00+00:00', posted_date='2026-01-01')[:2], ('unknown', 'show'))
        self.assertEqual(verdict(captured_at='2026-09-23T10:00:00+00:00')[:2], ('unknown', 'hide'))
        self.assertEqual(verdict(captured_at='2026-09-30T10:00:00+00:00', verification_state='CLOSED')[:2], ('closed', 'hide'))

    def test_the_limits_can_be_set(self):
        old = job(posted_date='2026-09-11')
        self.assertEqual(freshness(old, TODAY, max_age_days=10, check_age_days=20).decision, 'check')
        self.assertEqual(freshness(old, TODAY, max_age_days=10, check_age_days=15).decision, 'hide')
        with patch.object(settings, 'max_age_days', 10), patch.object(settings, 'check_age_days', 15):
            self.assertEqual(freshness(old, TODAY).decision, 'hide')

    def test_the_summary_says_why(self):
        self.assertIn('45 days', freshness(job(posted_date='2026-08-17'), TODAY).summary)
        self.assertIn('no posted date', freshness(job(), TODAY).summary.lower())
        self.assertIn('2026-09-30', freshness(job(last_listed_on='2026-09-30'), TODAY).summary)

    def test_today_is_a_date(self):
        self.assertIsInstance(today_ist(), date)


class _Stores(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        root = Path(directory.name)
        for module, name, value in ((history, 'DB_PATH', root / 'history.sqlite3'), (career_store, 'DB_PATH', root / 'agent.sqlite3'),
                                    (search_cache, 'DB_PATH', root / 'search_cache.sqlite3'),
                                    (alert_store, 'DB_PATH', root / 'alerts.sqlite3'),
                                    (profile_store, 'PROFILE_PATH', root / 'profile.json'),
                                    (preference_store, 'PREFERENCES_PATH', root / 'preferences.json'),
                                    (radar_store, 'DB_PATH', root / 'radar.sqlite3')):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)


class StoredDatesTests(_Stores):
    def test_a_radar_job_carries_the_day_its_board_last_listed_it(self):
        entry = company({'type': 'greenhouse', 'board': 'b'}, id='b', name='Example')
        listed = posting(entry, job_id='1', title='AI Engineer', location='Pune, India', description=FRESHER,
                         url='https://job-boards.greenhouse.io/b/jobs/1')
        gone = posting(entry, job_id='2', title='AI Engineer', location='Pune, India', description=FRESHER,
                       url='https://job-boards.greenhouse.io/b/jobs/2')
        radar_store.record_listing(entry.id, [listed, gone], complete=True, today=date(2026, 9, 28))
        radar_store.record_listing(entry.id, [listed], complete=True, today=date(2026, 9, 29))
        days = {found.source_job_id: (found.last_listed_on, found.verification_checked_at) for found in radar_store.list_jobs()}
        self.assertEqual(days, {'1': ('2026-09-29', '2026-09-29'), '2': ('2026-09-28', '2026-09-29')},
                         'a job missing from the board for a day was checked, but not seen')

    def test_only_a_saved_page_carries_its_capture_time(self):
        saved = job(application_url='https://example.com/jobs/1', source='Saved by you')
        alert = job(title='ML Engineer', source_job_id='indeed:1', source='Indeed alert')
        alert_store.upsert([saved], kind='capture')
        alert_store.upsert([alert], kind='alert')
        by_source = {found.source: found.captured_at for found in alert_store.list_jobs()}
        self.assertIsNone(by_source['Indeed alert'])
        self.assertTrue(str(by_source['Saved by you']).startswith(str(date.today().year)))


class SearchFreshnessTests(_Stores, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        profile_store.save_profile(CandidateProfile(graduation_year=2025, experience_years=0, skills=['Python', 'PyTorch'],
                                                    preferred_roles=['AI Engineer'], preferred_locations=['Pune']))
        entry = company({'type': 'greenhouse', 'board': 'b'}, id='b', name='Example')
        self.jobs = {name: posting(entry, job_id=name, title='AI Engineer', location='Pune, India', description=description,
                                   url=f'https://job-boards.greenhouse.io/b/jobs/{name}', posted=posted)
                     for name, description, posted in (('fresh', FRESHER, '2026-09-26'), ('check', FRESHER, '2026-08-17'),
                                                       ('stale', FRESHER, '2026-06-01'), ('senior', SENIOR, '2026-09-26'),
                                                       ('old-senior', SENIOR, '2026-06-01'))}
        radar_store.record_listing(entry.id, list(self.jobs.values()), complete=True, today=date(2026, 9, 28))

    async def search(self, **options):
        return await CareerAgent([RadarProvider()]).search('AI Engineer jobs for freshers in India', include_seen=True, **options)

    @staticmethod
    def names(results):
        return [result['source_job_id'] for result in results]

    async def test_stale_jobs_are_hidden_and_older_ones_form_a_check_group_at_the_end(self):
        response = await self.search(today=TODAY)
        self.assertEqual(self.names(response['results']), ['fresh', 'check'])
        self.assertEqual([result['freshness']['decision'] for result in response['results']], ['show', 'check'])
        self.assertEqual(response['results'][1]['freshness']['age_days'], 45)
        diagnostics = response['diagnostics']
        self.assertEqual((diagnostics['stale_hidden'], diagnostics['check_before_applying'], diagnostics['eligibility_rejected'],
                          diagnostics['final_recommendations']), (2, 1, 1, 2))
        self.assertIn('2 hidden as stale', response['summary'])

    async def test_the_check_group_stays_last_whatever_its_score(self):
        response = await self.search(today=TODAY)
        decisions = [result['freshness']['decision'] for result in response['results']]
        self.assertEqual(decisions, sorted(decisions, key=['show', 'check'].index))

    async def test_jobs_listed_within_48_hours_are_shown_at_any_age(self):
        response = await self.search(today=date(2026, 9, 30))
        self.assertEqual(sorted(self.names(response['results'])), ['check', 'fresh', 'stale'])
        self.assertEqual({result['freshness']['state'] for result in response['results']}, {'open_verified'})
        self.assertEqual(response['diagnostics']['stale_hidden'], 0)

    async def test_today_defaults_to_the_current_india_date(self):
        with patch('app.services.career_agent.today_ist', return_value=TODAY):
            response = await self.search()
        self.assertEqual(self.names(response['results']), ['fresh', 'check'])

    async def test_excluded_jobs_are_returned_only_on_request_and_never_stored(self):
        plain = await self.search(today=TODAY)
        self.assertNotIn('excluded', plain)
        response = await self.search(today=TODAY, return_excluded=True)
        self.assertEqual(self.names(response['results']), self.names(plain['results']))
        reasons = {item['source_job_id']: item['excluded_by'] for item in response['excluded']}
        self.assertEqual(reasons, {'stale': 'freshness', 'old-senior': 'freshness', 'senior': 'eligibility'})
        senior = next(item for item in response['excluded'] if item['source_job_id'] == 'senior')
        self.assertEqual((senior['eligibility_status'], senior['freshness']['decision']), ('excluded', 'show'))
        self.assertTrue(senior['eligibility_summary'] and senior['id'])
        stored = career_store.get_session(response['session_id'])['response']
        self.assertNotIn('excluded', stored)
        self.assertEqual(response['diagnostics']['provider_results'], len(response['results']) + len(response['excluded']))


if __name__ == '__main__':
    unittest.main()
