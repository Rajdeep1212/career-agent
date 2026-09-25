import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from app.models.schemas import JobPosting, CandidateProfile
from app.storage import history, profile_store, preference_store, career_store


class FakeProvider:
    name='Fictional provider'
    def __init__(self,jobs):self.jobs=jobs;self.queries=[]
    async def search(self,query,page=1):
        self.queries.append(query)
        return [job.model_copy(deep=True) for job in self.jobs]


class CareerAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        directory=Path(temporary.name)
        for module,name,value in [(history,'DB_PATH',directory/'history.db'),(career_store,'DB_PATH',directory/'agent.db'),
            (profile_store,'PROFILE_PATH',directory/'profile.json'),(preference_store,'PREFERENCES_PATH',directory/'prefs.json')]:
            p=patch.object(module,name,value);p.start();self.addCleanup(p.stop)
        profile_store.save_profile(CandidateProfile(name='Fictional Student',graduation_year=2025,skills=['Python','SQL','Excel'],experience_years=0))
        self.job=JobPosting(company='Example',title='Data Analyst',location='Kolkata',skills=['SQL','Excel'],
            description='Freshers welcome, 2025 graduates',fresher_allowed=True,application_url='https://example.com/jobs/123')

    async def test_multi_query_not_raw_prompt_and_diagnostics(self):
        from app.services.career_agent import CareerAgent
        provider=FakeProvider([self.job])
        query='Find Data Analyst jobs suitable for my CV in Kolkata with clear eligibility and matching explanations.'
        async def verified(job):
            job.verification_state='ACTIVE_VERIFIED';job.application_status='active';return job
        with patch('app.services.career_agent.verify_application',side_effect=verified):
            response=await CareerAgent([provider]).search(query)
        self.assertEqual(len(provider.queries),4)
        self.assertNotIn(query,provider.queries)
        self.assertTrue(all(len(q)<150 for q in provider.queries))
        self.assertEqual(response['diagnostics']['provider_results'],4)
        self.assertEqual(response['diagnostics']['unique_jobs'],1)
        self.assertEqual(response['result_count'],1)
        self.assertTrue(response['results'][0]['match']['explanation'])
        self.assertTrue(response['results'][0]['id'])
        self.assertTrue(response['session_id'])

    async def test_score_refinement_reuses_and_can_restore_stored_rankings(self):
        from app.services.career_agent import CareerAgent
        provider=FakeProvider([self.job])
        with patch('app.services.career_agent.verify_application',side_effect=lambda job:job):
            agent=CareerAgent([provider])
            initial=await agent.search('Find Data Analyst jobs')
            filtered=await agent.search('Show jobs above 100% match',session_id=initial['session_id'])
            restored=await agent.search('Show jobs above 0% match',session_id=initial['session_id'])
        self.assertEqual(filtered['result_count'],0)
        self.assertEqual(restored['result_count'],1)
        self.assertEqual(len(provider.queries),4)
        self.assertTrue(filtered['diagnostics']['reused_results'])

    async def test_unverified_shown_closed_rejected_and_seen_explained(self):
        from app.services.career_agent import CareerAgent
        closed=self.job.model_copy(update={'application_url':'https://example.com/jobs/456','verification_state':'CLOSED','application_status':'closed'})
        with patch('app.services.career_agent.verify_application',side_effect=lambda job:job):
            agent=CareerAgent([FakeProvider([self.job,closed])])
            first=await agent.search('Find Data Analyst jobs')
            second=await agent.search('Find Data Analyst jobs')
        self.assertEqual(first['result_count'],1)
        self.assertEqual(first['diagnostics']['closed'],1)
        self.assertEqual(first['diagnostics']['unverified'],1)
        self.assertEqual(second['diagnostics']['already_seen'],1)
        self.assertIn('already seen',second['summary'].lower())

    async def test_provider_failures_safe_and_zero_diagnostics(self):
        from app.services.career_agent import CareerAgent
        provider=FakeProvider([])
        provider.search=AsyncMock(side_effect=RuntimeError('secret-provider-body'))
        response=await CareerAgent([provider]).search('Find jobs suitable for my CV')
        self.assertEqual(response['result_count'],0)
        self.assertTrue(response['diagnostics']['errors'])
        self.assertNotIn('secret-provider-body',str(response))

    async def test_explicit_saved_location_reference_merges_preferences_into_planning(self):
        from app.services.career_agent import CareerAgent
        profile = profile_store.load_profile().model_copy(update={
            'preferred_locations': ['Kolkata', 'Pune', 'KOLKATA'],
        })
        profile_store.save_profile(profile)
        provider = FakeProvider([])

        response = await CareerAgent([provider]).search(
            'Find fresher jobs in India and prioritize my saved locations.'
        )

        self.assertEqual(response['intent']['locations'], ['Kolkata', 'Pune', 'India'])
        planned_locations = [item['location'] for item in response['queries']]
        self.assertEqual(planned_locations[:3], ['Kolkata', 'Pune', 'India'])
        self.assertEqual(len({location.casefold() for location in response['intent']['locations']}), 3)

    async def test_verification_has_bounded_concurrency(self):
        import asyncio
        from app.services.career_agent import CareerAgent
        jobs=[self.job.model_copy(update={'application_url':f'https://example.com/jobs/{i}'}) for i in range(12)]
        current=0;peak=0
        async def verify(job):
            nonlocal current,peak
            current+=1;peak=max(peak,current)
            await asyncio.sleep(.005)
            current-=1
            return job
        with patch('app.services.career_agent.verify_application',side_effect=verify):
            await CareerAgent([FakeProvider(jobs)]).search('Find Data Analyst jobs')
        self.assertGreater(peak,1)
        self.assertLessEqual(peak,4)
