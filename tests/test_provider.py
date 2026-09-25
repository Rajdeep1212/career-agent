import unittest
from unittest.mock import patch
import httpx
from app.providers.jsearch_provider import JSearchProvider
from app.core.config import settings


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_normalized_identity_employment_and_batch(self):
        from app.providers.jsearch_provider import normalize_item
        job = normalize_item({'job_id':'a1','employer_name':'Example','job_title':'QA Analyst',
            'job_description':'2025/2026 graduates. 0-2 years. Freshers welcome. Postman API testing SQL',
            'job_employment_type':'FULLTIME','job_apply_link':'https://example.com/job/1',
            'job_min_salary':30000,'job_max_salary':50000,'job_salary_currency':'INR'})
        self.assertEqual(job.source_job_id,'a1')
        self.assertEqual(job.graduation_years,[2025,2026])
        self.assertEqual(job.experience_max,2)
        self.assertEqual(job.employment_type,'FULLTIME')
        self.assertIn('INR',job.salary)

    async def test_error_body_never_escapes(self):
        client=httpx.AsyncClient
        def factory(**kwargs):
            return client(transport=httpx.MockTransport(lambda r:httpx.Response(403,text='PRIVATE-KEY-PROVIDER-BODY')), **kwargs)
        with patch.object(settings,'rapidapi_key','test-key'),patch('httpx.AsyncClient',side_effect=factory):
            with self.assertRaises(RuntimeError) as error:
                await JSearchProvider().search('QA Kolkata')
        self.assertNotIn('PRIVATE-KEY',str(error.exception))


class ProviderNormalizationRegressions(unittest.TestCase):
    def test_work_mode_is_unknown_without_evidence_and_negation_is_respected(self):
        from app.providers.jsearch_provider import normalize_item
        for item, expected in [({}, 'unknown'), ({'job_is_remote': False}, 'unknown'),
                ({'job_description': 'This is not a remote role.'}, 'onsite'),
                ({'job_description': 'No remote work. Office in Kolkata.'}, 'onsite'),
                ({'job_description': 'Hybrid schedule; not fully remote.'}, 'hybrid'),
                ({'job_description': 'Remote collaboration experience preferred.'}, 'unknown'),
                ({'job_description': 'Work from home position.'}, 'remote')]:
            with self.subTest(item=item):
                self.assertEqual(normalize_item(item).work_mode, expected)

    def test_malformed_application_links_do_not_abort_normalization(self):
        from app.providers.jsearch_provider import normalize_item
        for value in [123, {'url': 'https://example.com/job'}, ['https://example.com/job'], 'https://example.com:bad/job']:
            with self.subTest(value=value):
                job = normalize_item({'job_apply_link': value, 'job_title': 'QA Engineer'})
                self.assertIsNone(job.application_url)
                self.assertEqual(job.title, 'QA Engineer')
