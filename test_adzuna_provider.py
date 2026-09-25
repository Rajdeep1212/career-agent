"""Adzuna adapter: request shape, normalization, safe errors, and terms-driven choices."""
import unittest
from unittest.mock import patch

import httpx

from app.core.config import settings
from app.models.career import SearchQuery
from app.providers.adzuna_provider import AdzunaProvider, normalize_item
from app.providers.base import ProviderError

ITEM = {
    'id': '4711', 'title': 'Junior <strong>Data Analyst</strong>', 'created': '2026-09-24T08:00:00Z',
    'description': 'Freshers welcome. Python, SQL and Power BI&nbsp;dashboards.',
    'redirect_url': 'https://www.adzuna.in/land/ad/4711?se=abc',
    'company': {'display_name': 'Example Analytics'}, 'location': {'display_name': 'Pune, Maharashtra'},
    'salary_min': 300000, 'salary_max': 450000, 'salary_is_predicted': '0',
    'contract_time': 'full_time', 'contract_type': 'permanent',
}


class AdzunaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.multiple(settings, adzuna_app_id='test-id', adzuna_app_key='ADZUNA-SECRET', search_country='in')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.requests = []

    def _serve(self, response):
        original = httpx.AsyncClient

        def handler(request):
            self.requests.append(request)
            return response
        return patch('httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw))

    async def test_planned_query_uses_role_and_city(self):
        planned = SearchQuery(query='Data Analyst fresher Pune', reason='', role='Data Analyst', location='Pune')
        with self._serve(httpx.Response(200, json={'results': [ITEM]})):
            jobs = await AdzunaProvider().search_planned(planned)
        url = self.requests[0].url
        self.assertEqual((url.host, url.path), ('api.adzuna.com', '/v1/api/jobs/in/search/1'))
        self.assertEqual(url.params['what'], 'Data Analyst')
        self.assertEqual(url.params['where'], 'Pune')
        self.assertEqual(url.params['app_id'], 'test-id')
        self.assertEqual(len(jobs), 1)

    async def test_country_wide_and_remote_locations(self):
        with self._serve(httpx.Response(200, json={'results': []})):
            provider = AdzunaProvider()
            await provider.search_planned(SearchQuery(query='q', reason='', role='ML Engineer', location='India'))
            await provider.search_planned(SearchQuery(query='q', reason='', role='ML Engineer', location='remote India'))
        self.assertNotIn('where', self.requests[0].url.params)
        self.assertEqual(self.requests[1].url.params['what'], 'ML Engineer remote')
        self.assertNotIn('where', self.requests[1].url.params)

    async def test_identical_planned_queries_are_requested_once(self):
        provider = AdzunaProvider()
        with self._serve(httpx.Response(200, json={'results': [ITEM]})):
            for query in ('Data Analyst fresher Pune', 'Data Analyst graduate Pune'):
                jobs = await provider.search_planned(SearchQuery(query=query, reason='', role='Data Analyst', location='Pune'))
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len(jobs), 1)

    async def test_errors_name_status_without_body_or_key(self):
        for status, reason in ((401, 'invalid ADZUNA_APP_ID or ADZUNA_APP_KEY'),
                               (429, 'rate limit exceeded (default 25 a minute, 250 a day)'),
                               (502, 'provider server error')):
            self.requests.clear()
            with self._serve(httpx.Response(status, text='PRIVATE-BODY')):
                with self.assertRaises(ProviderError) as caught:
                    await AdzunaProvider().search('python')
            self.assertEqual((caught.exception.status_code, caught.exception.reason), (status, reason))
            for private in ('PRIVATE-BODY', 'ADZUNA-SECRET'):
                self.assertNotIn(private, str(caught.exception))

    def test_normalization(self):
        job = normalize_item(ITEM)
        self.assertEqual((job.title, job.company, job.location), ('Junior Data Analyst', 'Example Analytics', 'Pune, Maharashtra'))
        self.assertEqual(job.source, 'Adzuna')
        self.assertEqual(job.source_job_id, '4711')
        self.assertEqual(str(job.application_url), ITEM['redirect_url'])
        self.assertEqual(job.salary, '300000 450000')
        self.assertEqual(job.employment_type, 'full time permanent')
        self.assertTrue(job.fresher_allowed)
        self.assertIn('Power BI', job.skills)
        self.assertNotIn('&nbsp;', job.description)

    def test_predicted_salaries_are_not_shown(self):
        # Predicted "Jobsworth" salaries need separate Adzuna attribution.
        self.assertIsNone(normalize_item({**ITEM, 'salary_is_predicted': '1'}).salary)

    def test_configured_needs_both_values(self):
        with patch.multiple(settings, adzuna_app_id='id', adzuna_app_key=''):
            self.assertFalse(AdzunaProvider.configured())



class ProviderKeyLoggingTests(unittest.TestCase):
    def test_httpx_request_logs_redact_provider_keys(self):
        import logging
        from app.core.oauth_logging import OAuthQueryFilter
        for url in ('https://api.adzuna.com/v1/api/jobs/in/search/1?app_id=test-id&app_key=ADZUNA-SECRET&what=x',
                    'https://in.jooble.org/api/JOOBLE-SECRET'):
            record = logging.LogRecord('httpx', logging.INFO, '', 0, 'HTTP Request: %s %s "%s"',
                                       ('GET', url, 'HTTP/1.1 200 OK'), None)
            OAuthQueryFilter().filter(record)
            for secret in ('ADZUNA-SECRET', 'test-id', 'JOOBLE-SECRET'):
                self.assertNotIn(secret, record.getMessage())

if __name__ == '__main__':
    unittest.main()
