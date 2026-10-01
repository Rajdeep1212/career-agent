"""Local searches cover every saved location; the query budget limits paid providers only."""
import gc
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.models.career import SearchIntent, SearchQuery
from app.models.schemas import CandidateProfile
from app.providers.radar_provider import RadarProvider
from app.services.career_agent import CareerAgent
from app.services.search_planner import plan_local_queries, plan_search_queries
from app.sources.adapters import posting
from app.storage import career_store, db, history, preference_store, profile_store, radar_store
from fresh_helpers import pin_today
from radar_helpers import company
from test_radar_provider import FRESHER, FailingProvider

SAVED = ['Kolkata', 'Chennai', 'Bengaluru', 'Noida', 'Hyderabad', 'Pune', 'Gurugram', 'Remote India']


class LocalPlanTests(unittest.TestCase):
    def test_local_plan_has_every_role_and_saved_location(self):
        profile = CandidateProfile(preferred_locations=SAVED, preferred_roles=['Machine Learning Engineer'])
        intent = SearchIntent(roles_requested=['Machine Learning Engineer', 'Data Scientist'], locations=SAVED)
        local = plan_local_queries(profile, intent, [])
        pairs = {(query.role, query.location) for query in local}
        self.assertLessEqual({(role, place) for role in intent.roles_requested for place in SAVED}, pairs)
        self.assertEqual(len(plan_search_queries(profile, intent, [], 4)), 4)


class RemoteLocationTests(unittest.TestCase):
    def test_remote_location_matches_remote_jobs_only(self):
        entry = company({"type": "greenhouse", "board": "b"}, id="b")
        onsite = posting(entry, job_id='1', title='Machine Learning Engineer', location='Mumbai, India',
                         description=FRESHER, url='https://job-boards.greenhouse.io/b/jobs/1')
        remote = posting(entry, job_id='2', title='Machine Learning Engineer', location='Mumbai, India',
                         description=FRESHER, url='https://job-boards.greenhouse.io/b/jobs/2', work_mode='remote')
        from app.providers.radar_provider import _location_pattern, _matches
        place = _location_pattern('Remote India')
        self.assertFalse(_matches(onsite, 'Machine Learning Engineer', None, place))
        self.assertTrue(_matches(remote, 'Machine Learning Engineer', None, place))
        self.assertTrue(_matches(onsite, 'Machine Learning Engineer', None, _location_pattern('India')))


class LocalSearchCoverageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(gc.collect)
        root = Path(directory.name)
        from unittest.mock import patch
        for module, name, value in ((history, "DB_PATH", root / "history.sqlite3"), (career_store, "DB_PATH", root / "agent.sqlite3"),
                                    (profile_store, "PROFILE_PATH", root / "profile.json"),
                                    (preference_store, "PREFERENCES_PATH", root / "preferences.json"),
                                    (radar_store, "DB_PATH", root / "radar.sqlite3")):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        db.reset_cache()
        self.addCleanup(db.reset_cache)
        pin_today(self)   # the day the fixture jobs were listed
        profile_store.save_profile(CandidateProfile(graduation_year=2025, experience_years=0, skills=['Python', 'PyTorch'],
                                                    preferred_roles=['Machine Learning Engineer'], preferred_locations=SAVED))
        entry = company({"type": "greenhouse", "board": "b"}, id="b", name="Example")
        jobs = [posting(entry, job_id=str(index), title='Machine Learning Engineer', location=f'{city}, India',
                        description=FRESHER, url=f'https://job-boards.greenhouse.io/b/jobs/{index}')
                for index, city in enumerate(['Pune', 'Hyderabad', 'Gurugram', 'Mumbai'])]
        jobs.append(posting(entry, job_id='9', title='Machine Learning Engineer', location='Mumbai, India',
                            description=FRESHER, url='https://job-boards.greenhouse.io/b/jobs/9', work_mode='remote'))
        radar_store.record_listing(entry.id, jobs, complete=True, today=date(2026, 9, 28))

    async def test_later_saved_locations_are_searched_with_no_requests(self):
        response = await CareerAgent([RadarProvider(), FailingProvider()]).search('machine learning engineer', include_seen=True)
        self.assertEqual(response['diagnostics']['provider_results'], 4)
        self.assertEqual(response['diagnostics']['provider_requests'], 0)

    def test_the_plan_preview_still_counts_only_paid_requests(self):
        plan = CareerAgent([RadarProvider(), FailingProvider()]).plan('machine learning engineer')
        self.assertEqual(plan['provider_requests'], 0)
        self.assertLessEqual(len(plan['queries']), 10)


class SearchLocalSignatureTests(unittest.TestCase):
    def test_search_local_takes_planned_queries(self):
        self.assertEqual(RadarProvider().search_local([SearchQuery(query='x', reason='', role='x', location='Pune')]), [])


if __name__ == '__main__':
    unittest.main()
