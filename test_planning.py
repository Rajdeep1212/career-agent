import unittest
from app.models.schemas import CandidateProfile, JobPosting
from app.models.career import SearchIntent, RoleSuggestion


class PlanningTests(unittest.TestCase):
    def test_queries_concise_bounded_preserve_locations(self):
        from app.services.search_planner import plan_search_queries
        intent = SearchIntent(roles_requested=['QA Engineer'], locations=['Kolkata', 'Chennai'], fresher_preference=True)
        roles = [RoleSuggestion(family='QA', titles=['QA Engineer', 'API Tester', 'QA Analyst'], reason='Requested testing')]
        queries = plan_search_queries(CandidateProfile(), intent, roles)
        self.assertEqual(len(queries), 4)
        self.assertTrue(all(len(q.query) < 120 for q in queries))
        self.assertTrue(any('Kolkata' in q.query for q in queries))
        self.assertTrue(any('Chennai' in q.query for q in queries))
        self.assertEqual(len(set(q.query for q in queries)), 4)

    def test_arbitrary_title_kept_and_no_cartesian_explosion(self):
        from app.services.search_planner import plan_search_queries
        intent = SearchIntent(roles_requested=['Marine Biologist'], locations=['Lisbon', 'Porto', 'Faro'])
        queries = plan_search_queries(CandidateProfile(), intent, [])
        self.assertEqual(len(queries), 4)
        self.assertTrue(all('Marine Biologist' in q.query for q in queries))

    def test_identity_uses_specific_url_and_drops_tracking(self):
        from app.services.job_identity import job_identity, deduplicate_jobs
        a=JobPosting(company='Example', title='Analyst', location='India', application_url='https://example.com/jobs/1?utm_source=test')
        b=a.model_copy(update={'application_url':'https://example.com/jobs/1'})
        c=a.model_copy(update={'application_url':'https://example.com/jobs/2'})
        self.assertEqual(job_identity(a), job_identity(b))
        self.assertNotEqual(job_identity(a), job_identity(c))
        self.assertEqual(len(deduplicate_jobs([a,b,c])), 2)

    def test_provider_registry_declares_unavailable_connectors(self):
        from app.providers.registry import provider_status
        state = provider_status()
        self.assertFalse(next(p for p in state if p['id']=='linkedin_jobs')['available'])
